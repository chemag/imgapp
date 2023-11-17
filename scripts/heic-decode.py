#!/usr/bin/env python3

"""heic-decode.py

A test for android HEIC decoding.

The first function is decode heic files in an android device.
The script copies a heic file into an android device, and
then calls imgapp to read it (using `BitmapFactory.decodeFile()`)
into a `Bitmap`. Finally, it converts the `Bitmap` pixel
information (packed ARGB) into a packed RGBA file, which is the
output file.

The second function analyzes a heic file (or set of heic files)
providing the average and variance of each of the color
components (R, G, B, and A).
"""


import argparse
import glob
import magic
import math
import os
import subprocess
import sys
import tempfile


PROC_CHOICES = ["help", "decode", "analyze"]

INPREFERREDCOLORSPACE_CHOICES = [
    "ACES",
    "ACESCG",
    "ADOBE_RGB",
    "BT2020",
    "BT2020_HLG",
    "BT2020_PQ",
    "BT709",
    "CIE_LAB",
    "CIE_XYZ",
    "DCI_P3",
    "DISPLAY_P3",
    "EXTENDED_SRGB",
    "LINEAR_EXTENDED_SRGB",
    "LINEAR_SRGB",
    "NTSC_1953",
    "PRO_PHOTO_RGB",
    "SMPTE_C",
    "SRGB",
    "None",
]

default_values = {
    "debug": 0,
    "proc": "help",
    "width": -1,
    "height": -1,
    "inPreferredColorSpace": None,
    "tmpdir": "/sdcard",
    "infile": None,
    "outfile": None,
}


def run(command, **kwargs):
    debug = kwargs.get("debug", 0)
    dry_run = kwargs.get("dry_run", False)
    env = kwargs.get("env", None)
    stdin = subprocess.PIPE if kwargs.get("stdin", False) else None
    bufsize = kwargs.get("bufsize", 0)
    universal_newlines = kwargs.get("universal_newlines", False)
    default_close_fds = True if sys.platform == "linux2" else False
    close_fds = kwargs.get("close_fds", default_close_fds)
    shell = type(command) in (type(""), type(""))
    if debug > 0:
        print("running $ %s" % command)
    if dry_run:
        return 0, b"stdout", b"stderr"
    p = subprocess.Popen(  # noqa: E501
        command,
        stdin=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=bufsize,
        universal_newlines=universal_newlines,
        env=env,
        close_fds=close_fds,
        shell=shell,
    )
    # wait for the command to terminate
    if stdin is not None:
        out, err = p.communicate(stdin)
    else:
        out, err = p.communicate()
    returncode = p.returncode
    # clean up
    del p
    # return results
    return returncode, out, err


def get_heic_resolution(infile, debug):
    # 1. run heif-info
    command = f"heif-info {infile}"
    returncode, out, err = run(command, debug=debug)
    assert returncode == 0, "error: %s" % err
    # 2. parse heif-info output
    for line in out.splitlines():
        if line.startswith(b"image: "):
            # 'image: 1024x1024 (id=1), primary'
            width, height = (int(v) for v in line.decode("ascii").split()[1].split("x"))
    return width, height


def get_image_resolution(infile, debug):
    # 1. run ffprobe
    command = f"ffprobe -v 0 -of csv='p=0' -select_streams v:0 -show_entries stream=width,height {infile}"
    returncode, out, err = run(command, debug=debug)
    assert returncode == 0, "error: %s" % err
    # 2. parse ffprobe output
    width, height = (int(v) for v in out.decode("ascii").split(","))
    return width, height


class HistogramCounter:
    def __init__(self):
        self.bins = {}

    def add(self, val):
        self.bins[val] = 1 if val not in self.bins else self.bins[val] + 1

    def append(self, histogram_counter):
        for k, v in histogram_counter.bins.items():
            if k not in self.bins:
                self.bins[k] = 0
            self.bins[k] += histogram_counter.bins[k]

    def get_mean(self):
        # https://stackoverflow.com/q/50786699
        s = 0
        number_sum = 0
        for val, number in self.bins.items():
            s += number * val
            number_sum += number
        mean = s / number_sum
        return mean

    def get_stddev(self):
        # https://stackoverflow.com/a/50786849
        mean = self.get_mean()
        t = 0
        number_sum = 0
        for val, number in self.bins.items():
            t += number * (val - mean) ** 2
            number_sum += number
        stddev = math.sqrt(t / number_sum)
        return stddev


def analyze_rgba_file(infile, width, height, debug):
    with open(infile, "rb") as fin:
        contents = fin.read()
    i = 0
    # get histogram of R, G, B, and A samples
    R = HistogramCounter()
    G = HistogramCounter()
    B = HistogramCounter()
    A = HistogramCounter()
    while i < len(contents):
        R.add(contents[i])
        i += 1
        G.add(contents[i])
        i += 1
        B.add(contents[i])
        i += 1
        A.add(contents[i])
        i += 1
    return R, G, B, A


def analyze_file(infile, width, height, debug):
    file_extension = os.path.splitext(infile)
    mime_type = magic.detect_from_filename(infile).mime_type
    if file_extension == "rgba":
        (R, G, B, A) = analyze_rgba_file(infile, width, height, debug)
    else:
        raise AssertionError(f"unsupported file: {infile} mime_type: {mime_type}")
    # calculate the average and variance
    Rmean = round(R.get_mean())
    Rstddev = R.get_stddev()
    Gmean = round(G.get_mean())
    Gstddev = G.get_stddev()
    Bmean = round(B.get_mean())
    Bstddev = B.get_stddev()
    Amean = round(A.get_mean())
    Astddev = A.get_stddev()
    return (Rmean, Rstddev, Gmean, Gstddev, Bmean, Bstddev, Amean, Astddev)


def analyze_dir(directory, outfile, width, height, debug):
    # calculate all R/G/B/A values
    infile_list = glob.glob(os.path.join(directory, "*.rgba"))
    infile_list.append(glob.glob(os.path.join(directory, "*.heic")))
    infile_list.sort()
    results = []
    for infile in infile_list:
        (Rmean, Rstddev, Gmean, Gstddev, Bmean, Bstddev, Amean, Astddev) = analyze_file(
            infile, width, height, debug
        )
        results.append(
            [infile, Rmean, Rstddev, Gmean, Gstddev, Bmean, Bstddev, Amean, Astddev]
        )
    # write CSV output
    with open(outfile, "w") as fout:
        fout.write("filename,rmean,rstddev,gmean,gstddev,bmean,bstddev,amean,astddev\n")
        for (
            infile,
            Rmean,
            Rstddev,
            Gmean,
            Gstddev,
            Bmean,
            Bstddev,
            Amean,
            Astddev,
        ) in results:
            fout.write(
                f"{infile},{Rmean},{Rstddev},{Gmean},{Gstddev},{Bmean},{Bstddev},{Amean},{Astddev}\n"
            )


def decode_heic_using_imgapp(infile, outfile, inPreferredColorSpace, tmpdir, debug):
    # 1. push the file
    infile_name = os.path.split(infile)[1]
    infile_path = os.path.join(tmpdir, f"{infile_name}")
    command = f"adb push {infile} {infile_path}"
    returncode, out, err = run(command, debug=debug)
    assert returncode == 0, "error: %s" % err

    # 2. spin until the infile path is finished
    expected_size = os.stat(infile).st_size
    while True:
        command = f"adb shell stat -c %s {infile_path}"
        returncode, out, err = run(command, debug=debug)
        assert returncode == 0, "error: %s" % err
        size = int(out)
        if debug > 0:
            print(f"{infile_path} -> {size}")
        if size == expected_size:
            break

    # 3. run imgapp
    outfile = outfile if outfile else f"{infile}.rgba"
    outfile_name = os.path.split(outfile)[1]
    tmp_suffix = os.path.split(tempfile.NamedTemporaryFile().name)[1]
    outfile_name = f"{outfile_name}.{tmp_suffix}"
    outfile_path = os.path.join(tmpdir, f"{outfile_name}")
    inPreferredColorSpace_str = ""
    if inPreferredColorSpace is not None and inPreferredColorSpace != "None":
        inPreferredColorSpace_str = f"-e inPreferredColorSpace {inPreferredColorSpace}"
    command = f"adb shell am start -W -e decode a -e input {infile_path} {inPreferredColorSpace_str} -e output {outfile_path} com.facebook.imgapp/.MainActivity"
    returncode, out, err = run(command, debug=debug)
    assert returncode == 0, "error: %s" % err

    # 4. spin until the outfile path is done
    width, height = get_heic_resolution(infile, debug)
    expected_size = 4 * width * height
    while True:
        command = f"adb shell stat -c %s {outfile_path}"
        returncode, out, err = run(command, debug=debug)
        if returncode != 0 and b"No such file or directory" in err:
            continue
        assert returncode == 0, "error: %s" % err
        size = int(out)
        if debug > 0:
            print(f"{outfile_path} -> {size}")
        if size == expected_size:
            break

    # 5. pull the outfile
    command = f"adb pull {outfile_path} {outfile}"
    returncode, out, err = run(command, debug=debug)
    assert returncode == 0, "error: %s" % err


def get_options(argv):
    """Generic option parser.

    Args:
        argv: list containing arguments

    Returns:
        Namespace - An argparse.ArgumentParser-generated option object
    """
    # init parser
    # usage = 'usage: %prog [options] arg1 arg2'
    # parser = argparse.OptionParser(usage=usage)
    # parser.print_help() to get argparse.usage (large help)
    # parser.print_usage() to get argparse.usage (just usage line)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-d",
        "--debug",
        action="count",
        dest="debug",
        default=default_values["debug"],
        help="Increase verbosity (use multiple times for more)",
    )
    parser.add_argument(
        "--quiet",
        action="store_const",
        dest="debug",
        const=-1,
        help="Zero verbosity",
    )
    parser.add_argument(
        "--proc",
        action="store",
        type=str,
        dest="proc",
        default=default_values["proc"],
        choices=PROC_CHOICES,
        metavar="[%s]"
        % (
            " | ".join(
                PROC_CHOICES,
            )
        ),
        help="processing",
    )
    # 2-parameter setter using argparse.Action
    parser.add_argument(
        "--width",
        action="store",
        type=int,
        dest="width",
        default=default_values["width"],
        metavar="WIDTH",
        help=("use WIDTH width (default: %i)" % default_values["width"]),
    )
    parser.add_argument(
        "--height",
        action="store",
        type=int,
        dest="height",
        default=default_values["height"],
        metavar="HEIGHT",
        help=("HEIGHT height (default: %i)" % default_values["height"]),
    )

    class VideoSizeAction(argparse.Action):
        def __call__(self, parser, namespace, values, option_string=None):
            namespace.width, namespace.height = [int(v) for v in values[0].split("x")]

    parser.add_argument(
        "--size",
        action=VideoSizeAction,
        nargs=1,
        help="use <width>x<height>",
    )
    parser.add_argument(
        "--inPreferredColorSpace",
        action="store",
        type=str,
        dest="inPreferredColorSpace",
        default=default_values["inPreferredColorSpace"],
        choices=INPREFERREDCOLORSPACE_CHOICES,
        metavar="[%s]"
        % (
            " | ".join(
                INPREFERREDCOLORSPACE_CHOICES,
            )
        ),
        help="inPreferredColorSpace parameter",
    )
    parser.add_argument(
        "--tmpdir",
        action="store",
        dest="tmpdir",
        default=default_values["tmpdir"],
        metavar="TMPDIR",
        help=("TMPDIR tmpdir (default: %s)" % default_values["tmpdir"]),
    )

    parser.add_argument(
        "-i",
        "--infile",
        type=str,
        default=default_values["infile"],
        metavar="input-file",
        help="input file",
    )
    parser.add_argument(
        "-o",
        "--outfile",
        type=str,
        default=default_values["outfile"],
        metavar="output-file",
        help="output file",
    )
    # do the parsing
    options = parser.parse_args(argv[1:])
    # implement help
    if options.proc == "help":
        parser.print_help()
        sys.exit(0)

    return options


def main(argv):
    # parse options
    options = get_options(argv)
    # get infile/outfile
    if options.infile == "-":
        options.infile = "/dev/fd/0"
    if options.outfile == "-":
        options.outfile = "/dev/fd/1"
    # print results
    if options.debug > 0:
        print(options)
    # do something
    if options.proc == "decode":
        decode_heic_using_imgapp(
            options.infile,
            options.outfile,
            options.inPreferredColorSpace,
            options.tmpdir,
            options.debug,
        )
    elif options.proc == "analyze":
        if os.path.isdir(options.infile):
            analyze_dir(
                options.infile,
                options.outfile,
                options.width,
                options.height,
                options.debug,
            )
        elif os.path.isfile(options.infile):
            (
                Rmean,
                Rstddev,
                Gmean,
                Gstddev,
                Bmean,
                Bstddev,
                Amean,
                Astddev,
            ) = analyze_file(
                options.infile, options.width, options.height, options.debug
            )
            print(
                f"{options.infile},{Rmean},{Rstddev},{Gmean},{Gstddev},{Bmean},{Bstddev},{Amean},{Astddev}\n"
            )


if __name__ == "__main__":
    # at least the CLI program name: (CLI) execution
    main(sys.argv)
