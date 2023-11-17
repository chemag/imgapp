#!/usr/bin/env python3

"""heic-decode.py

A test for android HEIC decoding.

The first function is decode heic files in an android device.
The script copies a heic file into an android device, and
then calls imgapp to read it (using `BitmapFactory.decodeFile()`)
into a `Bitmap`. Finally, it converts the `Bitmap` pixel
information (packed ARGB) into a packed RGBA file, which is the
output file.

The second function analyzes an image file (or set of image files)
providing the average and variance of each of the color
components (R, G, B, and A). Currently it supports RGBA and
HEIC files.
"""


import argparse
import csv
import glob
import magic
import math
import os
import struct
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
    "infiles": None,
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
    # heic path is different
    mime_type = magic.detect_from_filename(infile).mime_type
    if mime_type == "image/heic":
        return get_heic_resolution(infile, debug)
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


def analyze_y4m_file(infile, debug):
    width, height = get_image_resolution(infile, debug)
    # convert to yuv444p for easier parsing
    tmp_file_yuv = infile + ".yuv"
    command = f"ffmpeg -y -i {infile} -f rawvideo -pix_fmt yuv444p {tmp_file_yuv}"
    returncode, out, err = run(command, debug=debug)
    assert returncode == 0, "error: %s" % err
    # read yuv444p file
    with open(tmp_file_yuv, "rb") as fin:
        contents = fin.read()
    # get histogram of Y, U, and V samples
    Y = HistogramCounter()
    U = HistogramCounter()
    V = HistogramCounter()
    plane_length = width * height
    for i in range(plane_length):
        Y.add(contents[i])
    for i in range(plane_length):
        U.add(contents[i + plane_length])
    for i in range(plane_length):
        V.add(contents[i + 2 * plane_length])
    return Y, U, V


def analyze_jpeg_file(infile, debug):
    # get width and height
    width, height = get_image_resolution(infile, debug)
    tmp_file = tempfile.NamedTemporaryFile().name
    tmp_file_y4m = tmp_file + ".y4m"
    tmp_file_rgba = tmp_file_y4m + ".rgba"
    # convert jpeg to y4m file
    command = f"ffmpeg -y -i {infile} {tmp_file_y4m}"
    returncode, out, err = run(command, debug=debug)
    assert returncode == 0, "error: %s" % err
    Y, U, V = analyze_y4m_file(tmp_file_y4m, debug)
    # convert to rgba file
    command = f"ffmpeg -y -i {tmp_file_y4m} -f rawvideo -pix_fmt rgba {tmp_file_rgba}"
    returncode, out, err = run(command, debug=debug)
    assert returncode == 0, "error: %s" % err
    R, G, B, A = analyze_rgba_file(tmp_file_rgba, width, height, debug)
    return R, G, B, A, Y, U, V


def analyze_png_file(infile, debug):
    # get width and height
    width, height = get_image_resolution(infile, debug)
    tmp_file = tempfile.NamedTemporaryFile().name
    tmp_file_rgba = tmp_file + ".rgba"
    tmp_file_y4m = tmp_file_rgba + ".y4m"
    # convert jpeg to rgba file
    command = f"ffmpeg -y -i {infile} -f rawvideo -pix_fmt rgba {tmp_file_rgba}"
    returncode, out, err = run(command, debug=debug)
    assert returncode == 0, "error: %s" % err
    R, G, B, A = analyze_rgba_file(tmp_file_rgba, width, height, debug)
    # convert to y4m file
    command = f"ffmpeg -y -f rawvideo -pixel_format rgba -video_size {width}x{height} -i {tmp_file_rgba} -pix_fmt yuv444p {tmp_file_y4m}"
    returncode, out, err = run(command, debug=debug)
    assert returncode == 0, "error: %s" % err
    Y, U, V = analyze_y4m_file(tmp_file_y4m, debug)
    return R, G, B, A, Y, U, V


def analyze_heic_file(infile, debug):
    # 0. init histogram of R, G, B, A, Y, U, and V samples
    R = HistogramCounter()
    G = HistogramCounter()
    B = HistogramCounter()
    A = HistogramCounter()
    Y = HistogramCounter()
    U = HistogramCounter()
    V = HistogramCounter()
    # 1. get list of items in heic file
    command = f"isobmff-parse.py --list-items -i {infile}"
    returncode, out, err = run(command, debug=debug)
    assert returncode == 0, "error: %s" % err
    # parse the item list
    item_list = []
    for row in csv.reader(out.decode("ascii").splitlines()):
        if row[0] == "item_id":
            continue
        item_id, item_type = row[0], row[2]
        item_list.append((item_id, item_type))
    # 2. check whether there is a grid for tiles
    tmp_file = tempfile.NamedTemporaryFile().name
    tmp_file_grid = tempfile.NamedTemporaryFile().name + ".grid.bin"
    is_grid = False
    for item_id, item_type in item_list:
        if item_type == "grid":
            is_grid = True
            command = f"isobmff-parse.py --extract-item -i {infile} --item-id {item_id} -o {tmp_file_grid}"
            returncode, out, err = run(command, debug=debug)
            assert returncode == 0, "error: %s" % err
            # parse the grid
            with open(tmp_file_grid, "rb") as fin:
                grid_contents = fin.read()
            # ISO/IEC 23008-12:2022 Section 6.6.2.3.2
            grid_unpack_fmt = "BBBB"
            version, flags, rows_minus_one, columns_minus_one = struct.unpack(
                grid_unpack_fmt, grid_contents[:4]
            )
            if flags == 0:
                grid_unpack_fmt_rem = ">HH"
            elif flags == 1:
                grid_unpack_fmt_rem = ">II"
            output_width, output_height = struct.unpack(
                grid_unpack_fmt_rem, grid_contents[4:]
            )
    # 3. extract all the tiles
    tmp_file_h265 = tmp_file + ".265"
    tmp_file_y4m = tmp_file_h265 + ".y4m"
    tmp_file_rgba = tmp_file_y4m + ".rgba"
    tile_id = -1
    for item_id, item_type in item_list:
        tile_id += 1
        if debug > 0:
            print(f"{infile=} {item_id=} {item_type=}")
        if item_type == "hvc1":
            # extract h265 file
            command = f"MP4Box -dump-item {item_id}:path={tmp_file_h265} {infile}"
            returncode, out, err = run(command, debug=debug)
            assert returncode == 0, "error: %s" % err
            # convert to y4m file
            command = f"ffmpeg -y -i {tmp_file_h265} {tmp_file_y4m}"
            returncode, out, err = run(command, debug=debug)
            assert returncode == 0, "error: %s" % err
            # get width and height
            width, height = get_image_resolution(tmp_file_y4m, debug)
            crop_filter = ""
            if is_grid:
                width_last_column = (
                    width + output_width - width * (columns_minus_one + 1)
                )
                height_last_column = (
                    height + output_height - height * (rows_minus_one + 1)
                )

                # check whether we need to cut the image
                def is_last_column(tile_id, columns_minus_one):
                    return tile_id % (columns_minus_one + 1) == columns_minus_one

                def is_last_row(tile_id, columns_minus_one, rows_minus_one):
                    return tile_id >= (columns_minus_one + 1) * rows_minus_one

                def is_last_tile(tile_id, columns_minus_one, rows_minus_one):
                    return is_last_column(tile_id, columns_minus_one) or is_last_row(
                        tile_id, columns_minus_one, rows_minus_one
                    )

                if is_last_tile(tile_id, columns_minus_one, rows_minus_one):
                    crop_filter = (
                        f"-vf crop={width_last_column}:{height_last_column}:x=0:y=0"
                    )
                elif is_last_column(tile_id, columns_minus_one):
                    crop_filter = f"-vf crop={width_last_column}:{height}:x=0:y=0"
                elif is_last_row(tile_id, columns_minus_one, rows_minus_one):
                    crop_filter = f"-vf crop={width}:{height_last_column}:x=0:y=0"
            # crop file if needed
            if crop_filter:
                tmp_file_y4m_cropped = tmp_file_y4m + ".y4m"
                command = (
                    f"ffmpeg -y -i {tmp_file_y4m} {crop_filter} {tmp_file_y4m_cropped}"
                )
                returncode, out, err = run(command, debug=debug)
                assert returncode == 0, "error: %s" % err
                Ytmp, Utmp, Vtmp = analyze_y4m_file(tmp_file_y4m_cropped, debug)
            else:
                Ytmp, Utmp, Vtmp = analyze_y4m_file(tmp_file_y4m, debug)
            Y.append(Ytmp)
            U.append(Utmp)
            V.append(Vtmp)

            # convert to rgba file
            command = f"ffmpeg -y -i {tmp_file_y4m} {crop_filter} -f rawvideo -pix_fmt rgba {tmp_file_rgba}"
            returncode, out, err = run(command, debug=debug)
            assert returncode == 0, "error: %s" % err
            Rtmp, Gtmp, Btmp, Atmp = analyze_rgba_file(
                tmp_file_rgba, width, height, debug
            )
            if debug > 0:
                print(
                    f"{infile=} {item_id=} {item_type=} Rtmp: {Rtmp.bins} Gtmp: {Gtmp.bins} Btmp: {Btmp.bins} Atmp: {Atmp.bins} Ytmp: {Ytmp.bins} Utmp: {Utmp.bins} Vtmp: {Vtmp.bins}"
                )
            R.append(Rtmp)
            G.append(Gtmp)
            B.append(Btmp)
            A.append(Atmp)
    return R, G, B, A, Y, U, V


def analyze_file(infile, width, height, debug):
    file_extension = os.path.splitext(infile)
    mime_type = magic.detect_from_filename(infile).mime_type
    if file_extension == "rgba":
        R, G, B, A = analyze_rgba_file(infile, width, height, debug)
        Y, U, V = None, None, None
    elif mime_type == "image/heic":
        R, G, B, A, Y, U, V = analyze_heic_file(infile, debug)
    elif mime_type == "image/jpeg":
        R, G, B, A, Y, U, V = analyze_jpeg_file(infile, debug)
    elif mime_type == "image/png":
        R, G, B, A, Y, U, V = analyze_png_file(infile, debug)
    else:
        infile = R = G = B = A = Y = U = V = None
    return infile, R, G, B, A, Y, U, V


def parse_histograms(R, G, B, A, Y, U, V):
    # calculate the average and variance
    if R is not None:
        Rmean = round(R.get_mean())
        Rstddev = R.get_stddev()
    else:
        Rmean = Rstddev = None
    if G is not None:
        Gmean = round(G.get_mean())
        Gstddev = G.get_stddev()
    else:
        Gmean = Gstddev = None
    if B is not None:
        Bmean = round(B.get_mean())
        Bstddev = B.get_stddev()
    else:
        Bmean = Bstddev = None
    if A is not None:
        Amean = round(A.get_mean())
        Astddev = A.get_stddev()
    else:
        Amean = Astddev = None
    if Y is not None:
        Ymean = round(Y.get_mean())
        Ystddev = Y.get_stddev()
    else:
        Ymean = Ystddev = None
    if U is not None:
        Umean = round(U.get_mean())
        Ustddev = U.get_stddev()
    else:
        Umean = Ustddev = None
    if V is not None:
        Vmean = round(V.get_mean())
        Vstddev = V.get_stddev()
    else:
        Vmean = Vstddev = None
    return (
        Rmean,
        Rstddev,
        Gmean,
        Gstddev,
        Bmean,
        Bstddev,
        Amean,
        Astddev,
        Ymean,
        Ystddev,
        Umean,
        Ustddev,
        Vmean,
        Vstddev,
    )


def analyze_dir(directory, outfile, width, height, debug):
    # calculate all R/G/B/A values
    infile_list = glob.glob(os.path.join(directory, "*.rgba"))
    infile_list.append(glob.glob(os.path.join(directory, "*.heic")))
    infile_list.sort()
    return analyze_files(infile_list, outfile, width, height, debug)


def analyze_files(infile_list, outfile, width, height, debug):
    results = []
    for infile in infile_list:
        (infile_full, R, G, B, A, Y, U, V) = analyze_file(infile, width, height, debug)
        if R is None:
            continue
        (
            Rmean,
            Rstddev,
            Gmean,
            Gstddev,
            Bmean,
            Bstddev,
            Amean,
            Astddev,
            Ymean,
            Ystddev,
            Umean,
            Ustddev,
            Vmean,
            Vstddev,
        ) = parse_histograms(R, G, B, A, Y, U, V)
        results.append(
            [
                infile_full,
                Rmean,
                Rstddev,
                Gmean,
                Gstddev,
                Bmean,
                Bstddev,
                Amean,
                Astddev,
                Ymean,
                Ystddev,
                Umean,
                Ustddev,
                Vmean,
                Vstddev,
            ]
        )
    # write CSV output
    with open(outfile, "w") as fout:
        fout.write(
            "filename,rmean,rstddev,gmean,gstddev,bmean,bstddev,amean,astddev,ymean,ystddev,umean,ustddev,vmean,vstddev\n"
        )
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
            Ymean,
            Ystddev,
            Umean,
            Ustddev,
            Vmean,
            Vstddev,
        ) in results:
            fout.write(
                f"{infile},{Rmean},{Rstddev},{Gmean},{Gstddev},{Bmean},{Bstddev},{Amean},{Astddev},{Ymean},{Ystddev},{Umean},{Ustddev},{Vmean},{Vstddev}\n"
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
    width, height = get_image_resolution(infile, debug)
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
        "--infiles",
        type=str,
        nargs="+",
        default=default_values["infiles"],
        metavar="input-files",
        help="input files",
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
    if options.infile == "-" or options.infile is None:
        options.infile = "/dev/fd/0"
    if options.outfile == "-" or options.outfile is None:
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
        if options.infiles is not None:
            analyze_files(
                options.infiles,
                options.outfile,
                options.width,
                options.height,
                options.debug,
            )
        elif os.path.isdir(options.infile):
            analyze_dir(
                options.infile,
                options.outfile,
                options.width,
                options.height,
                options.debug,
            )
        elif os.path.isfile(options.infile):
            (
                infile_full,
                R,
                G,
                B,
                A,
                Y,
                U,
                V,
            ) = analyze_file(
                options.infile, options.width, options.height, options.debug
            )
            (
                Rmean,
                Rstddev,
                Gmean,
                Gstddev,
                Bmean,
                Bstddev,
                Amean,
                Astddev,
                Ymean,
                Ystddev,
                Umean,
                Ustddev,
                Vmean,
                Vstddev,
            ) = parse_histograms(R, G, B, A, Y, U, V)
            print(
                f"{infile_full},{Rmean},{Rstddev},{Gmean},{Gstddev},{Bmean},{Bstddev},{Amean},{Astddev},{Ymean},{Ystddev},{Umean},{Ustddev},{Vmean},{Vstddev}\n"
            )


if __name__ == "__main__":
    # at least the CLI program name: (CLI) execution
    main(sys.argv)
