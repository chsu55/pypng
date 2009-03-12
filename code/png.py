#!/usr/bin/env python

# $Id$
# $URL$

# Incorporated into Bangai-O Development Tools by drj on 2009-02-11 from
# http://trac.browsershots.org/browser/trunk/pypng/lib/png.py?rev=2885

# png.py - PNG encoder in pure Python
# Copyright (C) 2006 Johann C. Rocholl <johann@browsershots.org>
# Portions Copyright (C) 2009 David Jones <drj@pobox.com>
# And probably portions Copyright (C) 2006 Nicko van Someren <nicko@nicko.org>
#
# Original concept by Johann C. Rocholl.
#
# LICENSE (The MIT License)
#
# Permission is hereby granted, free of charge, to any person
# obtaining a copy of this software and associated documentation files
# (the "Software"), to deal in the Software without restriction,
# including without limitation the rights to use, copy, modify, merge,
# publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so,
# subject to the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
# NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS
# BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN
# ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
# CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
#
# Changelog (recent first):
# 2009-03-11 David: interlaced bit depth < 8 (writing).
# 2009-03-10 David: interlaced bit depth < 8 (reading).
# 2009-03-04 David: Flat and Boxed pixel formats.
# 2009-02-26 David: Palette support (writing).
# 2009-02-23 David: Bit-depths < 8; better PNM support.
# 2006-06-17 Nicko: Reworked into a class, faster interlacing.
# 2006-06-17 Johann: Very simple prototype PNG decoder.
# 2006-06-17 Nicko: Test suite with various image generators.
# 2006-06-17 Nicko: Alpha-channel, grey-scale, 16-bit/plane support.
# 2006-06-15 Johann: Scanline iterator interface for large input files.
# 2006-06-09 Johann: Very simple prototype PNG encoder.


"""
Pure Python PNG Reader/Writer

This is an implementation of a subset of the PNG specification at
http://www.w3.org/TR/2003/REC-PNG-20031110 in pure Python. It reads
and writes PNG files with all allowable bit depths (1/2/4/8/16/24/32/48/64
bits per pixel): greyscale, RGB, RGBA, with 8 or 16 bits per layer, and
also colormapped images with palettes.  A number of options are
supported.

For help, type "import png; help(png)" in your python interpreter.

This file can also be used as a command-line utility to convert PNM
files to PNG. The interface is similar to that of the pnmtopng program
from the netpbm package. Type "python png.py --help" at the shell
prompt for usage and a list of options.
"""


__revision__ = '$Rev$'
__date__ = '$Date: 2009/03/12 $'
__author__ = '$Author: drj $'


from array import array
import itertools
import math
import struct
import sys
import zlib
# http://www.python.org/doc/2.4.4/lib/module-warnings.html
import warnings


_adam7 = ((0, 0, 8, 8),
          (4, 0, 8, 8),
          (0, 4, 4, 8),
          (2, 0, 4, 4),
          (0, 2, 2, 4),
          (1, 0, 2, 2),
          (0, 1, 1, 2))

def group(s, n):
    # See
    # http://www.python.org/doc/2.6/library/functions.html#zip
    return zip(*[iter(s)]*n)

def interleave_planes(ipixels, apixels, ipsize, apsize):
    """
    Interleave color planes, e.g. RGB + A = RGBA.

    Return an array of pixels consisting of the ipsize elements of data
    from each pixel in ipixels followed by the apsize elements of data
    from each pixel in apixels.  Conventionally ipixels and apixels are
    byte arrays so thet sizes are bytes, but it actually works with any
    arrays of the same type.  The output array is the same type as the
    input arrays which should be the same type as each other.
    """
    itotal = len(ipixels)
    atotal = len(apixels)
    newtotal = itotal + atotal
    newpsize = ipsize + apsize
    # Set up the output buffer
    # See http://www.python.org/doc/2.4.4/lib/module-array.html#l2h-1356
    out = array(ipixels.typecode)
    # It's annoying that there is no cheap way to set the array size :-(
    out.extend(ipixels)
    out.extend(apixels)
    # Interleave in the pixel data
    for i in range(ipsize):
        out[i:newtotal:newpsize] = ipixels[i:itotal:ipsize]
    for i in range(apsize):
        out[i+ipsize:newtotal:newpsize] = apixels[i:atotal:apsize]
    return out

def check_palette(palette):
    """Check a palette argument (to the Writer class) for validity.
    Returns the palette as a list if okay; raises an exception otherwise.
    """

    # None is the default and is allowed.
    if palette is None:
        return None

    p = list(palette)
    if not (0 < len(p) <= 256):
        raise ValueError("a palette must have between 1 and 255 entries")
    seen_triple = False
    for i,t in enumerate(p):
        if len(t) not in (3,4):
            raise ValueError(
              "palette entry %d: entries must be 3- or 4-tuples." % i)
        if len(t) == 3:
            seen_triple = 3
        if seen_triple and len(t) == 4:
            raise ValueError(
              "palette entry %d: all 4-tuples must precede all 3-tuples" % i)
        for x in t:
            if int(x) != x or not(0 <= x <= 255):
                raise ValueError(
                  "palette entry %d: values must be integer: 0 <= x <= 255" % i)
    return p

class Error(Exception):
    pass


class Writer:
    """
    PNG encoder in pure Python.
    """

    def __init__(self, width, height,
                 transparent=None,
                 background=None,
                 gamma=None,
                 greyscale=False,
                 alpha=False,
                 palette=None,
                 bitdepth=8,
                 bytes_per_sample=None, # deprecated
                 compression=None,
                 interlace=False,
                 chunk_limit=2**20):
        """
        Create a PNG encoder object.

        Arguments:
        width, height - size of the image in pixels
        transparent - create a tRNS chunk
        background - create a bKGD chunk
        gamma - create a gAMA chunk
        greyscale - input data is greyscale, not RGB
        alpha - input data has alpha channel (RGBA)
        palette - create a palettized image (color type 3)
        bitdepth - 1, 2, 4, 8, or 16
        compression - zlib compression level (1-9)
        chunk_limit - write multiple IDAT chunks to save memory

        If specified, the transparent and background parameters must
        be a tuple with three integer values for red, green, blue, or
        a simple integer (or singleton tuple) for a greyscale image.

        If specified, the gamma parameter must be a float value.

        For greyscale and palette images the PNG specification allows
        the bit depth to be less than 8.  For other types, bit depths
        less than 8 are rejected.

        The palette option, when specified, causes a palettized
        (colour-mapped) image to be created: the PNG color type is set
        to 3; greyscale must not be set; alpha must not be set;
        transparent must not be set; the bit depth must be 1,2,4, or 8.

	The palette argument value should be a sequence of 3- or
	4-tuples.  3-tuples specify RGB palette entries; 4-tuples
	specify RGBA palette entries.  If both 4-tuples and 3-tuples
	appear in the sequence then all the 4-tuples must come
	before all the 3-tuples.  A PLTE chunk is created; if there
	are 4-tuples then a tRNS chunk is created as well.  The
	PLTE chunk will contain all the RGB triples in the same
	sequence; the tRNS chunk will contain the alpha channel for
	all the 4-tuples, in the same sequence.  Palette entries
	are always 8-bit.

        The default for the compression argument is None; this does not
        mean no compression, rather it means that the default from the zlib
        module is used.
        """

        if width <= 0 or height <= 0:
            raise ValueError("width and height must be greater than zero")

        if alpha and transparent is not None:
            raise ValueError(
                "transparent color not allowed with alpha channel")

        if bytes_per_sample is not None:
            warnings.warn('use bitdepth instead of bytes_per_sample',
                          DeprecationWarning)
            if bytes_per_sample not in (0.125, 0.25, 0.5, 1, 2):
                raise ValueError("bytes per sample must be .125, .25, .5, 1, or 2")
            bitdepth = int(8*bytes_per_sample)
        del bytes_per_sample
        if bitdepth not in (1,2,4,8,16):
            raise ValueError("bitdepth must be 1, 2, 4, 8, or 16")

        if bitdepth < 8 and not greyscale and not palette:
            raise ValueError("color images must use bitdepth 8 or 16")
        if bitdepth > 8 and palette:
            raise ValueError("bit depth must be 8 or less for images with palette")

        if palette:
            if transparent is not None:
                raise ValueError("transparent and palette not compatible")
            if alpha:
                raise ValueError("alpha and palette not compatible")
            if greyscale:
                raise ValueError("greyscale and palette not compatible")

        if transparent is not None:
            if greyscale:
                if type(transparent) is not int:
                    raise ValueError(
                        "transparent color for greyscale must be integer")
            else:
                if not (len(transparent) == 3 and
                        type(transparent[0]) is int and
                        type(transparent[1]) is int and
                        type(transparent[2]) is int):
                    raise ValueError(
                        "transparent color must be a triple of integers")

        if background is not None:
            if greyscale:
                if type(background) is not int:
                    raise ValueError(
                        "background color for greyscale must be integer")
            else:
                if not (len(background) == 3 and
                        type(background[0]) is int and
                        type(background[1]) is int and
                        type(background[2]) is int):
                    raise ValueError(
                        "background color must be a triple of integers")

        # It's important that the true boolean values (greyscale, alpha,
        # colormap, interlace) are converted to bool because Iverson's
        # convention is relied upon later on.
        self.width = width
        self.height = height
        self.transparent = transparent
        self.background = background
        self.gamma = gamma
        self.greyscale = bool(greyscale)
        self.alpha = bool(alpha)
        self.colormap = bool(palette)
        self.bitdepth = int(bitdepth)
        self.compression = compression
        self.chunk_limit = chunk_limit
        self.interlace = bool(interlace)
        self.palette = check_palette(palette)

        self.color_type = 4*self.alpha + 2*(not greyscale) + 1*self.colormap
        assert self.color_type in (0,2,3,4,6)

        self.color_planes = (3,1)[self.greyscale or self.colormap]
        self.planes = self.color_planes + self.alpha
        # :todo: fix for bitdepth < 8
        self.psize = (self.bitdepth/8) * self.planes

    def make_palette(self):
        """Create the byte sequences for a PLTE and if necessary a tRNS
        chunk.  Returned as a pair (p, t).  t will be None if no tRNS
        chunk is necessary.
        """

        p = array('B')
        t = array('B')

        for x in self.palette:
            p.extend(x[0:3])
            if len(x) > 3:
                t.append(x[3])
        p = p.tostring()
        t = t.tostring()
        if t:
            return p,t
        return p,None

    def write_chunk(self, outfile, tag, data=''):
        """
        Write a PNG chunk to the output file, including length and checksum.
        """
        # http://www.w3.org/TR/PNG/#5Chunk-layout
        outfile.write(struct.pack("!I", len(data)))
        outfile.write(tag)
        outfile.write(data)
        checksum = zlib.crc32(tag)
        checksum = zlib.crc32(data, checksum)
        outfile.write(struct.pack("!i", checksum))

    def write(self, outfile, rows):
        """
        Write a PNG image to the output file.  `rows` should be an
        iterable that yields each row in boxed row flat pixel format.
        """
        # http://www.w3.org/TR/PNG/#5PNG-file-signature
        outfile.write(struct.pack("8B", 137, 80, 78, 71, 13, 10, 26, 10))

        # http://www.w3.org/TR/PNG/#11IHDR
        self.write_chunk(outfile, 'IHDR',
                         struct.pack("!2I5B", self.width, self.height,
                                     self.bitdepth, self.color_type,
                                     0, 0, self.interlace))

        # http://www.w3.org/TR/PNG/#11gAMA
        if self.gamma is not None:
            self.write_chunk(outfile, 'gAMA',
                             struct.pack("!L", int(round(self.gamma*1e5))))
        
        # Without a palette (PLTE chunk), ordering is relatively
        # relaxed.  With one, gAMA chunk must precede PLTE chunk which
        # must precede tRNS and bKGD.
        # See http://www.w3.org/TR/PNG/#5ChunkOrdering
        if self.palette:
            p,t = self.make_palette()
            self.write_chunk(outfile, 'PLTE', p)
            if t:
                # tRNS chunk is optional.  Only needed if palette entries
                # have alpha.
                self.write_chunk(outfile, 'tRNS', t)

        # http://www.w3.org/TR/PNG/#11tRNS
        if self.transparent is not None:
            if self.greyscale:
                self.write_chunk(outfile, 'tRNS',
                                 struct.pack("!1H", *self.transparent))
            else:
                self.write_chunk(outfile, 'tRNS',
                                 struct.pack("!3H", *self.transparent))

        # http://www.w3.org/TR/PNG/#11bKGD
        if self.background is not None:
            if self.greyscale:
                self.write_chunk(outfile, 'bKGD',
                                 struct.pack("!1H", *self.background))
            else:
                self.write_chunk(outfile, 'bKGD',
                                 struct.pack("!3H", *self.background))

        # http://www.w3.org/TR/PNG/#11IDAT
        if self.compression is not None:
            compressor = zlib.compressobj(self.compression)
        else:
            compressor = zlib.compressobj()

        # Choose an extend function based on the bitdepth.  The extend
        # function packs/decomposes the pixel values into bytes and
        # stuffs them onto the data array.
        data = array('B')
        if self.bitdepth == 8:
            extend = data.extend
        elif self.bitdepth == 16:
            # Decompose into bytes
            # samples per line
            spl = self.width * self.planes
            def extend(sl):
                fmt = '!%dH' % len(sl)
                data.extend(array('B', struct.pack(fmt, *sl)))
        else:
            # Pack into bytes
            assert self.bitdepth < 8
            # samples per byte
            spb = int(8/self.bitdepth)
            def extend(sl):
                a = array('B', sl)
                # Adding padding bytes so we can group into a whole
                # number of spb-tuples.
                l = float(len(a))
                extra = math.ceil(l / float(spb))*spb - l
                a.extend([0]*int(extra))
                # Pack into bytes
                l = group(a, spb)
                l = map(lambda e: reduce(lambda x,y:
                                           (x << self.bitdepth) + y, e), l)
                data.extend(l)

        for row in rows:
            data.append(0)
            extend(row)
            if len(data) > self.chunk_limit:
                compressed = compressor.compress(data.tostring())
                if len(compressed):
                    # print >> sys.stderr, len(data), len(compressed)
                    self.write_chunk(outfile, 'IDAT', compressed)
                data = array('B')
        if len(data):
            compressed = compressor.compress(data.tostring())
        else:
            compressed = ''
        flushed = compressor.flush()
        if len(compressed) or len(flushed):
            # print >> sys.stderr, len(data), len(compressed), len(flushed)
            self.write_chunk(outfile, 'IDAT', compressed + flushed)

        # http://www.w3.org/TR/PNG/#11IEND
        self.write_chunk(outfile, 'IEND')

    def write_array(self, outfile, pixels):
        """
        Write an array in flat row flat pixel format as a PNG file on
        the output file.
        """
        if self.interlace:
            self.write(outfile, self.array_scanlines_interlace(pixels))
        else:
            self.write(outfile, self.array_scanlines(pixels))

    def convert_pnm(self, infile, outfile):
        """
        Convert a PNM file containing raw pixel data into a PNG file
        with the parameters set in the writer object.  Works for PGM and
        PPM formats.
        """
        if self.interlace:
            pixels = array('B')
            pixels.fromfile(infile,
                            (self.bitdepth/8) * self.color_planes *
                            self.width * self.height)
            self.write(outfile, self.array_scanlines_interlace(pixels))
        else:
            self.write(outfile, self.file_scanlines(infile))

    def convert_ppm_and_pgm(self, ppmfile, pgmfile, outfile):
        """
        Convert a PPM and PGM file containing raw pixel data into a
        PNG outfile with the parameters set in the writer object.
        """
        pixels = array('B')
        pixels.fromfile(ppmfile,
                        (self.bitdepth/8) * self.color_planes *
                        self.width * self.height)
        apixels = array('B')
        apixels.fromfile(pgmfile,
                         (self.bitdepth/8) *
                         self.width * self.height)
        pixels = interleave_planes(pixels, apixels,
                                   (self.bitdepth/8) * self.color_planes,
                                   (self.bitdepth/8))
        if self.interlace:
            self.write(outfile, self.array_scanlines_interlace(pixels))
        else:
            self.write(outfile, self.array_scanlines(pixels))

    def file_scanlines(self, infile):
        """
        Generates boxed rows in flat pixel format, from the input file
        `infile`.
        """
        # Samples per line
        spl = self.width * self.planes
        row_bytes = spl
        if self.bitdepth > 8:
            assert self.bitdepth == 16
            row_bytes *= 2
            fmt = '>%dH' % spl
            def line():
                return array('H', struct.unpack(fmt, infile.read(row_bytes)))
        else:
            def line():
                scanline = array('B')
                scanline.fromfile(infile, row_bytes)
                return scanline
        for y in range(self.height):
            yield line()

    def array_scanlines(self, pixels):
        """
        Generates boxed rows (flat pixels) from flat rows (flat pixels)
        in an array.
        """

        # Values per row
        vpr = self.width * self.planes
        stop = 0
        for y in range(self.height):
            start = stop
            stop = start + vpr
            yield pixels[start:stop]

    def array_scanlines_interlace(self, pixels):
        """
        Generator for interlaced scanlines from an array.  `pixels` is
        the full source image in flat row flat pixel format.  The
        generator yields each scanline of the reduced passes in turn, in
        boxed row flat pixel format.
        """
        # http://www.w3.org/TR/PNG/#8InterlaceMethods
        # Array type.
        fmt = 'BH'[self.bitdepth > 8]
        # Value per row
        vpr = self.width * self.planes
        for xstart, ystart, xstep, ystep in _adam7:
            if xstart >= self.width:
                continue
            ppr = int(math.ceil((self.width-xstart)/float(xstep)))
            # number of values in reduced image row.
            row_len = ppr*self.planes
            for y in range(ystart, self.height, ystep):
                if xstep == 1:
                    offset = y * vpr
                    yield pixels[offset:offset+vpr]
                else:
                    row = array(fmt)
                    # There's no easier way to set the length of an array
                    row.extend(pixels[0:row_len])
                    offset = y * vpr + xstart * self.planes
                    end_offset = (y+1) * vpr
                    skip = self.planes * xstep
                    for i in range(self.planes):
                        row[i::self.planes] = \
                            pixels[offset+i:end_offset:skip]
                    yield row

def filter_scanline(type, line, fo, prev=None):
    """Apply a scanline filter to a scanline.  `type` specifies the
    filter type (0 to 4); `line` specifies the current (unfiltered)
    scanline as a sequence of bytes; `prev` specifies the previous
    (unfiltered) scanline as a sequence of bytes. `fo` specifies the
    filter offset; normally this is size of a pixel in bytes (the number
    of bytes per sample times the number of channels), but when this is
    < 1 (for bit depths < 8) then the filter offset is 1."""

    assert 0 <= type < 5

    # The output array.  Which, pathetically, we extend one-byte at a
    # time (fortunately this is linear).
    out = array('B', [type])

    def sub():
        ai = -fo
        for x in line:
            if ai >= 0:
                x = (x - line[ai]) & 0xff
            out.append(x)
            ai += 1
    def up():
        for i,x in enumerate(line):
            x = (x - prev[i]) & 0xff
            out.append(x)
    def average():
        ai = -fo
        for i,x in enumerate(line):
            if ai >= 0:
                x = (x - ((line[ai] + prev[i]) >> 1)) & 0xff
            else:
                x = (x - (prev[i] >> 1)) & 0xff
            out.append(x)
            ai += 1
    def paeth():
        # http://www.w3.org/TR/PNG/#9Filter-type-4-Paeth
        ai = -fo # also used for ci
        for i,x in enumerate(line):
            a = 0
            b = prev[i]
            c = 0

            if ai >= 0:
                a = line[ai]
                c = prev[ai]
            p = a + b - c
            pa = abs(p - a)
            pb = abs(p - b)
            pc = abs(p - c)
            if pa <= pb and pa <= pc: Pr = a
            elif pb <= pc: Pr = b
            else: Pr = c

            x = (x - Pr) & 0xff
            out.append(x)
            ai += 1

    if not prev:
        # We're on the first line.  Some of the filters can be reduced
        # to simpler cases which makes handling the line "off the top"
        # of the image simpler.  "up" becomes "none"; "paeth" becomes
        # "left" (non-trivial, but true). "average" needs to be handled
        # specially.
        if type == 2: # "up"
            return line # type = 0
        elif type == 3:
            prev = [0]*len(line)
        elif type == 4: # "paeth"
            type = 1
    if type == 0:
        out.extend(line)
    elif type == 1:
        sub()
    elif type == 2:
        up()
    elif type == 3:
        average()
    else: # type == 4
        paeth()
    return out


class _readable:
    """
    A simple file-like interface for strings and arrays.
    """

    def __init__(self, buf):
        self.buf = buf
        self.offset = 0

    def read(self, n):
        r = self.buf[self.offset:self.offset+n]
        if isinstance(r, array):
            r = r.tostring()
        self.offset += n
        return r


class Reader:
    """
    PNG decoder in pure Python.
    """

    def __init__(self, _guess=None, **kw):
        """
        Create a PNG decoder object.

        The constructor expects exactly one keyword argument. If you
        supply a positional argument instead, it will guess the input
        type. You can choose among the following arguments:
        filename - name of PNG input file
        file - object with a read() method
        bytes - array or string with PNG data

        """
        if ((_guess is not None and len(kw) != 0) or
            (_guess is None and len(kw) != 1)):
            raise TypeError("Reader() takes exactly 1 argument")

        # Will be the first 8 bytes, later on.  See validate_signature.
        self.signature = None
        # A pair of (len,type) if a chunk has been read but its data and
        # checksum have not (in other words the file position is just
        # past the 4 bytes that specify the chunk type).  See preamble
        # method for how this is used.
        self.atchunk = None

        if _guess is not None:
            if isinstance(_guess, array):
                kw["bytes"] = _guess
            elif isinstance(_guess, str):
                kw["filename"] = _guess
            elif isinstance(_guess, file):
                kw["file"] = _guess

        if "filename" in kw:
            self.file = file(kw["filename"], "rb")
        elif "file" in kw:
            self.file = kw["file"]
        elif "bytes" in kw:
            self.file = _readable(kw["bytes"])
        else:
            raise TypeError("expecting filename, file or bytes array")

    def chunk(self, seek=None):
        """
        Read the next PNG chunk from the input file; returns type (as a 4
        character string) and data.  If the optional `seek` argument is
        specified then it will keep reading chunks until it either runs
        out of file or finds the type specified by the argument.  Note
        that in general the order of chunks in PNGs is unspecified, so
        using `seek` can cause you to miss chunks.
        """
        self.validate_signature()

        while True:
            # http://www.w3.org/TR/PNG/#5Chunk-layout
            if not self.atchunk:
                self.atchunk = self.chunklentype()
            length,type = self.atchunk
            self.atchunk = None
            data = self.file.read(length)
            if len(data) != length:
                raise ValueError('Chunk %s too short for required %i octets'
                                 % (type, length))
            checksum = self.file.read(4)
            if len(checksum) != 4:
                raise ValueError('Chunk %s too short for checksum', tag)
            if seek and type != seek:
                continue
            verify = zlib.crc32(type)
            verify = zlib.crc32(data, verify)
            verify = struct.pack('!i', verify)
            if checksum != verify:
                # print repr(checksum)
                (a, ) = struct.unpack('!I', checksum)
                (b, ) = struct.unpack('!I', verify)
                raise ValueError("Checksum error in %s chunk: 0x%X != 0x%X"
                                 % (type, a, b))
            return type, data

    def undo_filter(self, filter_type, scanline, previous):
        """Undo the filter for a scanline.  `scanline` is a sequence of
        bytes that does not include the initial filter type byte.
        `previous` is decoded previous scanline (for straightlaced
        images this is the previous pixel row, but for interlaced
        images, it is the previous scanline in the reduced image, which
        in general is not the previous pixel row in the final image).
        When there is no previous scanline (the first row of a
        straightlaced image, or the first row in one of the passes in an
        interlaced image), then this argument should be None.

        The scanline will have the effects of filtering removed, and the
        result will be returned as a fresh sequence of bytes."""

        # :todo: Would it be better to update scanline in place?

        # Create the result byte array.  It seems that the best way to
        # create the array to be the right size is to copy from an
        # existing sequence.  *sigh*
        # If we fill the result with scanline, then this allows a
        # micro-optimisation in the "null" and "sub" cases.
        result = array('B', scanline)

        if filter_type == 0:
            # And here, we _rely_ on filling the result with scanline,
            # above.
            return result

        # Filter unit.  The stride from one pixel to the corresponding
        # byte from the previous previous.  Normally this is the pixel
        # size in bytes, but when this is smaller than 1, the previous
        # byte is used instead.
        fu = max(1, self.psize)

        # For the first line of a pass, synthesize a dummy previous
        # line.  An alternative approach would be to observe that on the
        # first line 'up' is the same as 'null', 'paeth' is the same
        # as 'sub', with only 'average' requiring any special case.
        if not previous:
            previous = array('B', [0]*len(scanline))

        def sub():
            """Undo sub filter."""

            ai = 0
            # Loops starts at index fu.  Observe that the initial part
            # of the result is already filled in correctly with
            # scanline.
            for i in range(fu, len(result)):
                x = scanline[i]
                a = result[ai]
                result[i] = (x + a) & 0xff
                ai += 1

        def up():
            """Undo up filter."""

            for i in range(len(result)):
                x = scanline[i]
                b = previous[i]
                result[i] = (x + b) & 0xff

        def average():
            """Undo average filter."""

            ai = -fu
            for i in range(len(result)):
                x = scanline[i]
                if ai < 0:
                    a = 0
                else:
                    a = result[ai]
                b = previous[i]
                result[i] = (x + ((a + b) >> 1)) & 0xff
                ai += 1

        def paeth():
            """Undo Paeth filter."""

            # Also used for ci.
            ai = -fu
            for i in range(len(result)):
                x = scanline[i]
                if ai < 0:
                    a = c = 0
                else:
                    a = result[ai]
                    c = previous[ai]
                b = previous[i]
                p = a + b - c
                pa = abs(p - a)
                pb = abs(p - b)
                pc = abs(p - c)
                if pa <= pb and pa <= pc:
                    pr = a
                elif pb <= pc:
                    pr = b
                else:
                    pr = c
                result[i] = (x + pr) & 0xff
                ai += 1

        # Call appropriate filter algorithm.  Note that 0 has already
        # been dealth with.
        (None, sub, up, average, paeth)[filter_type]()
        return result

    def deinterlace(self, raw):
        """
        Read raw pixel data, undo filters, deinterlace, and flatten.
        Return in flat row flat pixel format.
        """

        # print >> sys.stderr, ("Reading interlaced, w=%s, r=%s, planes=%s," +
        #     " bpp=%s") % (self.width, self.height, self.planes, self.bps)
        # Values per row (of the target image)
        vpr = self.width * self.planes

        # Make a result array, and make it big enough.  Interleaving
        # writes to the output array randomly (well, not quite), so the
        # entire output array must be in memory.
        fmt = 'BH'[self.bitdepth > 8]
        a = array(fmt, [0]*vpr*self.height)
        source_offset = 0

        for xstart, ystart, xstep, ystep in _adam7:
            # print >> sys.stderr, "Adam7: start=%s,%s step=%s,%s" % (
            #     xstart, ystart, xstep, ystep)
            if xstart >= self.width:
                continue
            # The previous (reconstructed) scanline.  None at the
            # beginning of a pass to indicate that there is no previous
            # line.
            recon = None
            # Pixels per row (reduced pass image)
            ppr = int(math.ceil((self.width-xstart)/float(xstep)))
            # Row size in bytes for this pass.
            row_size = int(math.ceil(self.psize * ppr))
            for y in range(ystart, self.height, ystep):
                filter_type = raw[source_offset]
                source_offset += 1
                scanline = raw[source_offset:source_offset+row_size]
                source_offset += row_size
                recon = self.undo_filter(filter_type, scanline, recon)
                # Convert so that there is one element per pixel value
                flat = self.serialtoflat(recon, ppr)
                if xstep == 1:
                    assert xstart == 0
                    offset = y * vpr
                    a[offset:offset+vpr] = flat
                else:
                    offset = y * vpr + xstart * self.planes
                    end_offset = (y+1) * vpr
                    skip = self.planes * xstep
                    for i in range(self.planes):
                        a[offset+i:end_offset:skip] = \
                            flat[i::self.planes]
        return a

    def serialtoflat(self, bytes, width=None):
        """Convert serial format (byte stream) pixel data to flat row
        flat pixel."""

        if self.bitdepth == 8:
            return bytes
        if self.bitdepth == 16:
            bytes = bytes.tostring()
            return array('H',
              struct.unpack('!%dH' % (len(bytes)//2), bytes))
        else:
            assert self.bitdepth < 8
            if width is None:
                width = self.width
            # Samples per byte
            spb = 8//self.bitdepth
            out = array('B')
            mask = 2**self.bitdepth - 1
            shifts = map(self.bitdepth.__mul__, reversed(range(spb)))
            l = width
            for o in bytes:
                out.extend(map(lambda i: mask&(o>>i), shifts)[:l])
                l -= spb
                if l <= 0:
                    l = width
            return out

    def straightlaced(self, raw):
        """
        Read raw scanline data, undo the filters, and return as
        serialised (byte stream) pixels.
        """
        # length of row, in bytes
        rb = self.row_bytes
        a = array('B')
        offset = 0
        source_offset = 0
        # The previous (reconstructed) scanline.  None indicates first
        # line of image.
        recon = None
        for y in range(self.height):
            filter_type = raw[source_offset]
            source_offset += 1
            scanline = array('B', raw[source_offset:source_offset+rb])
            recon = self.undo_filter(filter_type, scanline, recon)
            a.extend(recon)
            offset += rb
            source_offset += rb
        return a

    def validate_signature(self):
        """If signature (header) has not been read then read and
        validate it; otherwise do nothing."""

        if self.signature:
            return
        self.signature = self.file.read(8)
        if self.signature != struct.pack("8B", 137, 80, 78, 71, 13, 10, 26, 10):
            raise Error("PNG file has invalid header")

    def preamble(self):
        """
        Extract the image metadata by reading the initial part of the PNG
        file up to the start of the IDAT chunk.  All the chunks that
        precede the IDAT chunk are read and either processed for
        metadata or discarded.
        """

        self.validate_signature()

        while True:
            if not self.atchunk:
                self.atchunk = self.chunklentype()
            if self.atchunk[1] == 'IDAT':
                return
            self.process_chunk()

    def chunklentype(self):
        return struct.unpack('!I4s', self.file.read(8))

    def process_chunk(self):
        """Process the next chunk and its data.  This only processes the
        following chunk types, all others are ignored: IHDR, PLTE, bKGD,
        tRNS, gAMA.
        """

        type, data = self.chunk()
        if type == 'IHDR':
            # http://www.w3.org/TR/PNG/#11IHDR
            (self.width, self.height, self.bitdepth, self.color_type,
             self.compression, self.filter,
             self.interlace) = struct.unpack("!2I5B", data)

            # Check that the header specifies only valid combinations.
            if self.bitdepth not in (1,2,4,8,16):
                raise Error("invalid bit depth %d" % self.bitdepth)
            if self.color_type not in (0,2,3,4,6):
                raise Error("invalid color type %d" % self.color_type)
            # Check indexed (palettized) images have 8 or fewer bits
            # per pixel; check only indexed or greyscale images have
            # fewer than 8 bits per pixel.
            if ((self.color_type & 1 and self.bitdepth > 8) or
                (self.bitdepth < 8 and self.color_type not in (0,3))):
                raise Error("illegal combination of bit depth (%d)"
                            " and color type (%d)"
                  % (self.bitdepth, self.color_type))
            if self.compression != 0:
                raise Error("unknown compression method %d" % self.compression)
            if self.filter != 0:
                raise Error("unknown filter method %d" % self.filter)
            if self.interlace not in (0,1):
                raise Error("illegal interlace method, %d" % self.interlace)

            # Derived values
            # http://www.w3.org/TR/PNG/#6Colour-values
            colormap =  bool(self.color_type & 1)
            greyscale = not (self.color_type & 2)
            alpha = bool(self.color_type & 4)
            color_planes = (3,1)[greyscale or colormap]
            planes = color_planes + alpha

            self.colormap = colormap
            self.greyscale = greyscale
            self.alpha = alpha
            self.color_planes = color_planes
            self.planes = planes
            self.psize = float(self.bitdepth)/float(8) * planes
            if int(self.psize) == self.psize:
                self.psize = int(self.psize)
            self.row_bytes = int(math.ceil(self.width * self.psize))
            # Stores PLTE chunk if present, and is used to check
            # chunk ordering constraints.
            self.plte = None
            # Stores tRNS chunk if present, and is used to check chunk
            # ordering constraints.
            self.trns = None
        elif type == 'PLTE':
            # http://www.w3.org/TR/PNG/#11PLTE
            if self.plte:
                warnings.warn("multiple PLTE chunks present")
            self.plte = data
            if len(data) % 3 != 0:
                warnings.warn("PLTE chunk's length should be a multiple of 3")
            if len(data) > (2**self.bitdepth)*3:
                warnings.warn("PLTE chunk is too long")
            if len(data) == 0:
                warnings.warn("empty PLTE is not allowed")
        elif type == 'bKGD':
            try:
                if self.colormap:
                    if not self.plte:
                        warnings.warn(
                          "PLTE chunk is required before bKGD chunk")
                    self.background = struct.unpack('B', data)
                else:
                    self.background = struct.unpack("!%dH" % self.color_planes,
                      data)
            except struct.error:
                raise ValueError("bKGD chunk has incorrect length")
        elif type == 'tRNS':
            # http://www.w3.org/TR/PNG/#11tRNS
            self.trns = data
            if self.colormap:
                if not self.plte:
                    warnings.warn("PLTE chunk is required before tRNS chunk")
                else:
                    if len(data) > len(self.plte)/3:
                        warnings.warn("tRNS chunk is too long")
                self.alpha = True
            else:
                if self.alpha:
                    raise Error("tRNS chunk is not valid with colortype %d" %
                                self.colortype)
                try:
                    self.transparent = \
                        struct.unpack("!%dH" % self.color_planes, data)
                except struct.error:
                    raise ValueError("tRNS chunk has incorrect length")
        elif type == 'gAMA':
            try:
                self.gamma = struct.unpack("!L", data)[0] / 100000.0
            except struct.error:
                raise ValueError("gAMA chunk has incorrect length")

    def read(self):
        """
        Read a simple PNG file; return width, height, pixels and image
        metadata.

        This function is a very early prototype with limited flexibility
        and excessive use of memory.

        pixels are returned in flat row flat pixel format.
        """

        self.preamble()
        compressed = []
        while True:
            try:
                type, data = self.chunk()
            except ValueError, e:
                raise Error('Chunk error: ' + e.args[0])

            # print >> sys.stderr, type, len(data)
            if type == 'IDAT':
            # http://www.w3.org/TR/PNG/#11IDAT
                if self.colormap and not self.plte:
                    warnings.warn("PLTE chunk is required before IDAT chunk")
                compressed.append(data)
            elif type == 'IEND': # http://www.w3.org/TR/PNG/#11IEND
                break
        packedlines = array('B', zlib.decompress(''.join(compressed)))
        if self.interlace:
            pixels = self.deinterlace(packedlines)
        else:
            pixels = self.serialtoflat(self.straightlaced(packedlines))
        meta = dict()
        for attr in 'greyscale alpha bitdepth interlace'.split():
            meta[attr] = getattr(self, attr)
        for attr in 'gamma transparent background'.split():
            a = getattr(self, attr, None)
            if a is not None:
                meta[attr] = a
        return self.width, self.height, pixels, meta

    def asRGB8(self):
        """Return the image data as an RGB image with 8-bits per
        sample.  Greyscales are expanded into RGB triplets; bit depths
        less than 8 are scaled up to 8-bits; bit depths greater than
        8 are scaled down to 8 (note: no dithering is performed).
        An alpha channel will raise an exception.

        This function returns a 4-tuple:
        (width, height, pixels, metadata).
        width, height, metadata are as per the read method.
        
        `pixels` is the pixel data in boxed row boxed pixel format.  It is
        an iterator that yields each row.  A row is a
        sequence of pixels; each pixel is an (R,G,B) triple with each
        channel from 0 to 255.
        """

        return self.asRGB8Aopt(3)

    def asRGBA8(self):
        """Return the image data as an RGBA image with 8-bits per
        sample.  Greyscales are expanded into RGB triplets; an Alpha
        channel is synthesized if necessary.  Otherwise performs same as
        asRGB8().
        """

        return self.asRGB8Aopt(4)

    def asRGB8Aopt(self, n, dropalpha=False):
        """n is the number of channels in the target."""
        assert n in (3,4)

        # :todo: remove assert
        assert not dropalpha

        def grey1():
            """Handle greyscales of 1-,2-, and 4- bits."""
            # Factor to convert from N-bits to 8-bit
            factor = maxval / (2**bitdepth - 1)
            # Number of pixels remaining in scanline
            w = width
            scanline = []
            for pixel in data:
                scanline.append((pixel*factor,)*3)
                if n == 4:
                    scanline.append(maxval)
                w -= 1
                if 0 == w:
                    w = width
                    # Making each scanline a tuple is consistent with rgb8().
                    yield tuple(scanline)
                    scanline = []
        def rgb8():
            """Handle RGB8 (and RGBA8)."""
            return iter(group(group(data, n), width))
        def rgb8add():
            assert n == 4
            return iter(group(map(lambda p: p + (maxval,),
                                  group(data, 3), width)))
        if 3 == n:
            def grey8():
                """Handle K8."""
                return iter(group(zip(data, data, data), width))
        else:
            def grey8():
                return iter(group(zip(data, data, data, maxval), width))
        def scaledown(scanline):
            """Helper used by grey16 and rgb16.  Convert a scanline of
            samples from 16-bits to 8-bit.  Input is a sequence of 16-bit
            integers."""

            def treatsample(x):
                """Convert 16-bit sample to 8-bit.  Just a little bit
                long for a lambda.
                """
                # Philosophically, rint would be preferred over round.
                # But in this case we are dividing an integer by an odd
                # integer (divisor is 257), so the answer can never have
                # a fractional value of exactly 0.5.  So round is good.
                return int(round(x/divisor))

            # We expect this to be an integer of course, but we need the
            # answer as a float.
            divisor = float(65535)/float(maxval)
            scanline = map(treatsample, scanline)
            return scanline
        def grey16():
            """Handle K16."""
            for scanline in group(data, self.width):
                scanline = scaledown(scanline)
                # expand into triples
                if 3 == n:
                    yield tuple(zip(scanline, scanline, scanline))
                if 4 == n:
                    yield tuple(zip(scanline, scanline, scanline, maxval))
        def rgb16():
            """Handle RGB16."""
            for scanline in group(data, self.width * self.planes):
                scanline = scaledown(scanline)
                yield tuple(group(scanline, n))
        def rgb16add():
            for scanline in group(data, self.width * self.planes):
                scanline = scaledown(scanline)
                yield tuple(map(lambda p: p + (maxval,),
                                group(scanline, 3)))
        def palette():
            """Handle palette, color type 3."""
            for scanline in group(data, self.width):
                scanline = map(plte.__getitem__, scanline)
                yield tuple(scanline)

        width, height, data, meta = self.read()
        bitdepth = meta["bitdepth"]
        targetdepth = 8
        # Target maxval
        maxval = 2**targetdepth - 1
        if 4 == n and meta["greyscale"] and meta["alpha"]:
            raise NotImplementedError("colortype 4 not supported")
        if 3 == n and meta["alpha"] and not dropalpha:
            raise Error("will not convert image with alpha channel to RGB")
        if self.colormap:
            if not self.plte:
                raise Error("required PLTE chunk is missing in color type 3 image")
            plte = group(array('B', self.plte), 3)
            if n == 4:
                # http://www.python.org/doc/2.4.4/lib/module-operator.html
                import operator
                trns = array('B', self.trns or '')
                trns.extend([255]*(len(plte)-len(trns)))
                plte = map(operator.add, plte, group(trns, 1))
            return width, height, palette(), meta
        elif bitdepth < 8:
            # assert grey because the colormap case is handled
            # separately, above.
            assert meta["greyscale"]
            return width, height, grey1(), meta
        elif bitdepth == 8:
            if meta["greyscale"]:
                return width, height, grey8(), meta
            else:
                if n == self.planes:
                    return width, height, rgb8(), meta
                elif n == 4:
                    return width, height, rgb8add(), meta
                elif n == 3:
                    return width, height, rgb8drop(), meta
        elif bitdepth == 16:
            if meta["greyscale"]:
                return width, height, grey16(), meta
            else:
                if n == self.planes:
                    return width, height, rgb16(), meta
                elif n == 4:
                    return width, height, rgb16add(), meta
                elif n == 3:
                    return width, height, rgb16drop(), meta
        assert False, "Illegal bit-depth of %d" % bitdepth

# === Internal Test Support ===

# This section comprises the tests that are internally validated (as
# opposed to tests which produce output files that are externally
# validated).  Primarily they are unittests.

# Run them from the command line with:
# python -c 'import png;png.test()'

# http://www.python.org/doc/2.4.4/lib/module-unittest.html
import unittest
from StringIO import StringIO

def test():
    unittest.main(__name__)

def topngbytes(name, array, x, y, **k):
    """Convenience function for creating a PNG file "in memory" as a
    string.  Creates a Writer instance using the keyword arguments, then
    passes the array to its write_array method.  The resulting PNG file
    is returned as a string.  `name` is used to identify the file for
    debugging.
    """

    print name
    f = StringIO()
    w = Writer(x, y, **k)
    w.write_array(f, array)
    if False:
        w = open(name, 'wb')
        w.write(f.getvalue())
        w.close()
    return f.getvalue()

class Test(unittest.TestCase):
    def helperKN(self, n):
        mask = (1 << n) - 1
        w = Writer(15, 17, greyscale=True, bitdepth=n)
        f = StringIO()
        w.write_array(f, array('B', map(mask.__and__, range(1, 256))))
        r = Reader(bytes=f.getvalue())
        x,y,pixels,meta = r.read()
        self.assertEqual(x, 15)
        self.assertEqual(y, 17)
        self.assertEqual(list(pixels), map(mask.__and__, range(1,256)))
    def testK8(self):
        return self.helperKN(8)
    def testK4(self):
        return self.helperKN(4)
    def testK2(self):
        "Also tests asRGB8."
        w = Writer(1, 4, greyscale=True, bitdepth=2)
        f = StringIO()
        w.write_array(f, array('B', range(4)))
        r = Reader(bytes=f.getvalue())
        x,y,pixels,meta = r.asRGB8()
        self.assertEqual(x, 1)
        self.assertEqual(y, 4)
        for i,row in enumerate(pixels):
            self.assertEqual(len(row), 1)
            self.assertEqual(list(row[0]), [0x55*i]*3)
    def testP2(self):
        "2-bit palette."
        a = (255,255,255)
        b = (200,120,120)
        c = (50,99,50)
        w = Writer(1, 4, bitdepth=2, palette=[a,b,c])
        f = StringIO()
        w.write_array(f, array('B', (0,1,1,2)))
        r = Reader(bytes=f.getvalue())
        x,y,pixels,meta = r.asRGB8()
        self.assertEqual(x, 1)
        self.assertEqual(y, 4)
        self.assertEqual(list(pixels), zip([a, b, b, c]))
    def testPtrns(self):
        "Test color type 3 and tRNS chunk (and 4-bit palette)."
        a = (50,99,50,50)
        b = (200,120,120,80)
        c = (255,255,255)
        d = (200,120,120)
        e = (50,99,50)
        w = Writer(3, 3, bitdepth=4, palette=[a,b,c,d,e])
        f = StringIO()
        w.write_array(f, array('B', (4, 3, 2, 3, 2, 0, 2, 0, 1)))
        r = Reader(bytes=f.getvalue())
        x,y,pixels,meta = r.asRGBA8()
        self.assertEquals(x, 3)
        self.assertEquals(y, 3)
        c = c+(255,)
        d = d+(255,)
        e = e+(255,)
        self.assertEqual(list(pixels), [(e,d,c),(d,c,a),(c,a,b)])
    def testAdam7read(self):
        """Test Adam7 interlacing.  Specifically, test that for images
        in the PngSuite that have both an interlaced and straightlaced
        pair that both images from the pair produce the same array of
        pixels."""
        for candidate in _pngsuite:
            if not candidate.startswith('basn'):
                continue
            candi = candidate.replace('n', 'i')
            if candi not in _pngsuite:
                continue
            print 'adam7 read', candidate
            straight = Reader(bytes=_pngsuite[candidate])
            adam7 = Reader(bytes=_pngsuite[candi])
            # metadata is ignored because the "interlace" member
            # differs.  Lame.
            self.assertEqual(straight.read()[:3], adam7.read()[:3])
    def testAdam7write(self):
        """Test Adam7 interlacing.  For each test image in the PngSuite,
        write an interlaced and a srtaightlaced version.  Decode both,
        and compare results.
        """
        # Not such a great test, because the only way we can check what
        # we have written is to read it back again.

        for name,bytes in _pngsuite.items():
            # Only certain colour types supported for this test.
            if name[3:5] not in ['n0', 'n2', 'n4', 'n6']:
                continue
            it = Reader(bytes=bytes)
            x,y,pixels,meta = it.read()
            pngi = topngbytes('adam7wn'+name+'.png', pixels,
              x=x, y=y, bitdepth=it.bitdepth,
              greyscale=it.greyscale, alpha=it.alpha,
              interlace=False)
            x,y,ps,meta = Reader(bytes=pngi).read()
            pngs = topngbytes('adam7wi'+name+'.png', pixels,
              x=x, y=y, bitdepth=it.bitdepth,
              greyscale=it.greyscale, alpha=it.alpha,
              interlace=True)
            x,y,pi,meta = Reader(bytes=pngs).read()
            self.assertEqual(ps, pi)


# === Command Line Support ===

def _dehex(s):
    """Liberally convert from hex string to binary string."""
    import re

    # Remove all non-hexadecimal digits
    s = re.sub(r'[^a-fA-F\d]', '', s)
    return s.decode('hex')

# Copies of PngSuite test files taken
# from http://www.schaik.com/pngsuite/pngsuite_bas_png.html
# on 2009-02-19 by drj and converted to hex.
_pngsuite = dict(
  basi0g01=_dehex("""
89504e470d0a1a0a0000000d49484452000000200000002001000000012c0677
cf0000000467414d41000186a031e8965f0000009049444154789c2d8d310ec2
300c45dfc682c415187a00a42e197ab81e83b127e00c5639001363a580d8582c
65c910357c4b78b0bfbfdf4f70168c19e7acb970a3f2d1ded9695ce5bf5963df
d92aaf4c9fd927ea449e6487df5b9c36e799b91bdf082b4d4bd4014fe4014b01
ab7a17aee694d28d328a2d63837a70451e1648702d9a9ff4a11d2f7a51aa21e5
a18c7ffd0094e3511d661822f20000000049454e44ae426082
"""),
  basi0g02=_dehex("""
89504e470d0a1a0a0000000d49484452000000200000002002000000016ba60d
1f0000000467414d41000186a031e8965f0000005149444154789c635062e860
00e17286bb609c93c370ec189494960631366e4467b3ae675dcf10f521ea0303
90c1ca006444e11643482064114a4852c710baea3f18c31918020c30410403a6
0ac1a09239009c52804d85b6d97d0000000049454e44ae426082
"""),
  basi0g04=_dehex("""
89504e470d0a1a0a0000000d4948445200000020000000200400000001e4e6f8
bf0000000467414d41000186a031e8965f000000ae49444154789c658e5111c2
301044171c141c141c041c843a287510ea20d441c041c141c141c04191102454
03994998cecd7edcecedbb9bdbc3b2c2b6457545fbc4bac1be437347f7c66a77
3c23d60db15e88f5c5627338a5416c2e691a9b475a89cd27eda12895ae8dfdab
43d61e590764f5c83a226b40d669bec307f93247701687723abf31ff83a2284b
a5b4ae6b63ac6520ad730ca4ed7b06d20e030369bd6720ed383290360406d24e
13811f2781eba9d34d07160000000049454e44ae426082
"""),
  basi0g08=_dehex("""
89504e470d0a1a0a0000000d4948445200000020000000200800000001211615
be0000000467414d41000186a031e8965f000000b549444154789cb5905d0ac2
3010849dbac81c42c47bf843cf253e8878b0aa17110f214bdca6be240f5d21a5
94ced3e49bcd322c1624115515154998aa424822a82a5624a1aa8a8b24c58f99
999908130989a04a00d76c2c09e76cf21adcb209393a6553577da17140a2c59e
70ecbfa388dff1f03b82fb82bd07f05f7cb13f80bb07ad2fd60c011c3c588eef
f1f4e03bbec7ce832dca927aea005e431b625796345307b019c845e6bfc3bb98
769d84f9efb02ea6c00f9bb9ff45e81f9f280000000049454e44ae426082
"""),
  basi0g16=_dehex("""
89504e470d0a1a0a0000000d49484452000000200000002010000000017186c9
fd0000000467414d41000186a031e8965f000000e249444154789cb5913b0ec2
301044c7490aa8f85d81c3e4301c8f53a4ca0da8902c8144b3920b4043111282
23bc4956681a6bf5fc3c5a3ba0448912d91a4de2c38dd8e380231eede4c4f7a1
4677700bec7bd9b1d344689315a3418d1a6efbe5b8305ba01f8ff4808c063e26
c60d5c81edcf6c58c535e252839e93801b15c0a70d810ae0d306b205dc32b187
272b64057e4720ff0502154034831520154034c3df81400510cdf0015c86e5cc
5c79c639fddba9dcb5456b51d7980eb52d8e7d7fa620a75120d6064641a05120
b606771a05626b401a05f1f589827cf0fe44c1f0bae0055698ee8914fffffe00
00000049454e44ae426082
"""),
  basi2c08=_dehex("""
89504e470d0a1a0a0000000d49484452000000200000002008020000018b1fdd
350000000467414d41000186a031e8965f000000f249444154789cd59341aa04
210c44abc07b78133d59d37333bd89d76868b566d10cf4675af8596431a11662
7c5688919280e312257dd6a0a4cf1a01008ee312a5f3c69c37e6fcc3f47e6776
a07f8bdaf5b40feed2d33e025e2ff4fe2d4a63e1a16d91180b736d8bc45854c5
6d951863f4a7e0b66dcf09a900f3ffa2948d4091e53ca86c048a64390f662b50
4a999660ced906182b9a01a8be00a56404a6ede182b1223b4025e32c4de34304
63457680c93aada6c99b73865aab2fc094920d901a203f5ddfe1970d28456783
26cffbafeffcd30654f46d119be4793f827387fc0d189d5bc4d69a3c23d45a7f
db803146578337df4d0a3121fc3d330000000049454e44ae426082
"""),
  basi2c16=_dehex("""
89504e470d0a1a0a0000000d4948445200000020000000201002000001db8f01
760000000467414d41000186a031e8965f0000020a49444154789cd5962173e3
3010853fcf1838cc61a1818185a53e56787fa13fa130852e3b5878b4b0b03081
b97f7030070b53e6b057a0a8912bbb9163b9f109ececbc59bd7dcf2b45492409
d66f00eb1dd83cb5497d65456aeb8e1040913b3b2c04504c936dd5a9c7e2c6eb
b1b8f17a58e8d043da56f06f0f9f62e5217b6ba3a1b76f6c9e99e8696a2a72e2
c4fb1e4d452e92ec9652b807486d12b6669be00db38d9114b0c1961e375461a5
5f76682a85c367ad6f682ff53a9c2a353191764b78bb07d8ddc3c97c1950f391
6745c7b9852c73c2f212605a466a502705c8338069c8b9e84efab941eb393a97
d4c9fd63148314209f1c1d3434e847ead6380de291d6f26a25c1ebb5047f5f24
d85c49f0f22cc1d34282c72709cab90477bf25b89d49f0f351822297e0ea9704
f34c82bc94002448ede51866e5656aef5d7c6a385cb4d80e6a538ceba04e6df2
480e9aa84ddedb413bb5c97b3838456df2d4fec2c7a706983e7474d085fae820
a841776a83073838973ac0413fea2f1dc4a06e71108fda73109bdae48954ad60
bf867aac3ce44c7c1589a711cf8a81df9b219679d96d1cec3d8bbbeaa2012626
df8c7802eda201b2d2e0239b409868171fc104ba8b76f10b4da09f6817ffc609
c413ede267fd1fbab46880c90f80eccf0013185eb48b47ba03df2bdaadef3181
cb8976f18e13188768170f98c0f844bb78cb04c62ddac59d09fc3fa25dfc1da4
14deb3df1344f70000000049454e44ae426082
"""),
  basi3p08=_dehex("""
89504e470d0a1a0a0000000d494844520000002000000020080300000133a3ba
500000000467414d41000186a031e8965f00000300504c5445224400f5ffed77
ff77cbffff110a003a77002222ffff11ff110000222200ffac5566ff66ff6666
ff01ff221200dcffffccff994444ff005555220000cbcbff44440055ff55cbcb
00331a00ffecdcedffffe4ffcbffdcdc44ff446666ff330000442200ededff66
6600ffa444ffffaaeded0000cbcbfefffffdfffeffff0133ff33552a000101ff
8888ff00aaaa010100440000888800ffe4cbba5b0022ff22663200ffff99aaaa
ff550000aaaa00cb630011ff11d4ffaa773a00ff4444dc6b0066000001ff0188
4200ecffdc6bdc00ffdcba00333300ed00ed7300ffff88994a0011ffff770000
ff8301ffbabafe7b00fffeff00cb00ff999922ffff880000ffff77008888ffdc
ff1a33000000aa33ffff009900990000000001326600ffbaff44ffffffaaff00
770000fefeaa00004a9900ffff66ff22220000998bff1155ffffff0101ff88ff
005500001111fffffefffdfea4ff4466ffffff66ff003300ffff55ff77770000
88ff44ff00110077ffff006666ffffed000100fff5ed1111ffffff44ff22ffff
eded11110088ffff00007793ff2200dcdc3333fffe00febabaff99ffff333300
63cb00baba00acff55ffffdcffff337bfe00ed00ed5555ffaaffffdcdcff5555
00000066dcdc00dc00dc83ff017777fffefeffffffcbff5555777700fefe00cb
00cb0000fe010200010000122200ffff220044449bff33ffd4aa0000559999ff
999900ba00ba2a5500ffcbcbb4ff66ff9b33ffffbaaa00aa42880053aa00ffaa
aa0000ed00babaffff1100fe00000044009999990099ffcc99ba000088008800
dc00ff93220000dcfefffeaa5300770077020100cb0000000033ffedff00ba00
ff3333edffedffc488bcff7700aa00660066002222dc0000ffcbffdcffdcff8b
110000cb00010155005500880000002201ffffcbffcbed0000ff88884400445b
ba00ffbc77ff99ff006600baffba00777773ed00fe00003300330000baff77ff
004400aaffaafffefe000011220022c4ff8800eded99ff99ff55ff002200ffb4
661100110a1100ff1111dcffbabaffff88ff88010001ff33ffb98ed362000002
a249444154789c65d0695c0b001806f03711a9904a94d24dac63292949e5a810
d244588a14ca5161d1a1323973252242d62157d12ae498c8124d25ca3a11398a
16e55a3cdffab0ffe7f77d7fcff3528645349b584c3187824d9d19d4ec2e3523
9eb0ae975cf8de02f2486d502191841b42967a1ad49e5ddc4265f69a899e26b5
e9e468181baae3a71a41b95669da8df2ea3594c1b31046d7b17bfb86592e4cbe
d89b23e8db0af6304d756e60a8f4ad378bdc2552ae5948df1d35b52143141533
33bbbbababebeb3b3bc9c9c9c6c6c0c0d7b7b535323225a5aa8a02024a4bedec
0a0a2a2bcdcd7d7cf2f3a9a9c9cdcdd8b8adcdd5b5ababa828298982824a4ab2
b21212acadbdbc1414e2e24859b9a72730302f4f49292c4c57373c9c0a0b7372
8c8c1c1c3a3a92936d6dfdfd293e3e26262a4a4eaea2424b4b5fbfbc9c323278
3c0b0ba1303abaae8ecdeeed950d6669a9a7a7a141d4de9e9d5d5cdcd2229b94
c572716132f97cb1d8db9bc3110864a39795d9db6b6a26267a7a9a98d4d6a6a7
cb76090ef6f030354d4d75766e686030545464cb393a1a1ac6c68686eae8f8f9
a9aa4644c8b66d6e1689dcdd2512a994cb35330b0991ad9f9b6b659596a6addd
d8282fafae5e5323fb8f41d01f76c22fd8061be01bfc041a0323e1002c81cd30
0b9ec027a0c930014ec035580fc3e112bc069a0b53e11c0c8095f00176c163a0
e5301baec06a580677600ddc05ba0f13e120bc81a770133ec355a017300d4ec2
0c7800bbe1219c02fa08f3e13c1c85dbb00a2ec05ea0dff00a6ec15a98027360
070c047a06d7e1085c84f1b014f6c03fa0b33018b6c0211801ebe018fc00da0a
6f61113c877eb01d4ec317a085700f26c130f80efbe132bc039a0733e106fc81
f7f017f6c10aa0d1300a0ec374780943e1382c06fa0a9b60238c83473016cec0
02f80f73fefe1072afc1e50000000049454e44ae426082
"""),
  basi6a08=_dehex("""
89504e470d0a1a0a0000000d4948445200000020000000200806000001047d4a
620000000467414d41000186a031e8965f0000012049444154789cc595414ec3
3010459fa541b8bbb26641b8069b861e8b4d12c1c112c1452a710a2a65d840d5
949041fc481ec98ae27c7f3f8d27e3e4648047600fec0d1f390fbbe2633a31e2
9389e4e4ea7bfdbf3d9a6b800ab89f1bd6b553cfcbb0679e960563d72e0a9293
b7337b9f988cc67f5f0e186d20e808042f1c97054e1309da40d02d7e27f92e03
6cbfc64df0fc3117a6210a1b6ad1a00df21c1abcf2a01944c7101b0cb568a001
909c9cf9e399cf3d8d9d4660a875405d9a60d000b05e2de55e25780b7a5268e0
622118e2399aab063a815808462f1ab86890fc2e03e48bb109ded7d26ce4bf59
0db91bac0050747fec5015ce80da0e5700281be533f0ce6d5900b59bcb00ea6d
200314cf801faab200ea752803a8d7a90c503a039f824a53f4694e7342000000
0049454e44ae426082
"""),
  basn0g01=_dehex("""
89504e470d0a1a0a0000000d49484452000000200000002001000000005b0147
590000000467414d41000186a031e8965f0000005b49444154789c2dccb10903
300c05d1ebd204b24a200b7a346f90153c82c18d0a61450751f1e08a2faaead2
a4846ccea9255306e753345712e211b221bf4b263d1b427325255e8bdab29e6f
6aca30692e9d29616ee96f3065f0bf1f1087492fd02f14c90000000049454e44
ae426082
"""),
  basn0g02=_dehex("""
89504e470d0a1a0a0000000d49484452000000200000002002000000001ca13d
890000000467414d41000186a031e8965f0000001f49444154789c6360085df5
1f8cf1308850c20053868f0133091f6390b90700bd497f818b0989a900000000
49454e44ae426082
"""),
  basn0g04=_dehex("""
89504e470d0a1a0a0000000d494844520000002000000020040000000093e1c8
290000000467414d41000186a031e8965f0000004849444154789c6360601014
545232367671090d4d4b2b2f6720430095dbd1418e002a77e64c720450b9ab56
912380caddbd9b1c0154ee9933e408a072efde25470095fbee1d1902001f14ee
01eaff41fa0000000049454e44ae426082
"""),
  basn0g08=_dehex("""
89504e470d0a1a0a0000000d4948445200000020000000200800000000561125
280000000467414d41000186a031e8965f0000004149444154789c6364602400
1408c8b30c05058c0f0829f8f71f3f6079301c1430ca11906764a2795c0c0605
8c8ff0cafeffcff887e67131181430cae0956564040050e5fe7135e2d8590000
000049454e44ae426082
"""),
  basn0g16=_dehex("""
89504e470d0a1a0a0000000d49484452000000200000002010000000000681f9
6b0000000467414d41000186a031e8965f0000005e49444154789cd5d2310ac0
300c4351395bef7fc6dca093c0287b32d52a04a3d98f3f3880a7b857131363a0
3a82601d089900dd82f640ca04e816dc06422640b7a03d903201ba05b7819009
d02d680fa44c603f6f07ec4ff41938cf7f0016d84bd85fae2b9fd70000000049
454e44ae426082
"""),
  basn2c16=_dehex("""
89504e470d0a1a0a0000000d4948445200000020000000201002000000ac8831
e00000000467414d41000186a031e8965f000000e549444154789cd596c10a83
301044a7e0417fcb7eb7fdadf6961e06039286266693cc7a188645e43dd6a08f
1042003e2fe09aef6472737e183d27335fcee2f35a77b702ebce742870a23397
f3edf2705dd10160f3b2815fe8ecf2027974a6b0c03f74a6e4192843e75c6c03
35e8ec3202f5e84c0181bbe8cca967a00d9df3491bb040671f2e6087ce1c2860
8d1e05f8c7ee0f1d00b667e70df44467ef26d01fbd9bc028f42860f71d188bce
fb8d3630039dbd59601e7ab3c06cf428507f0634d039afdc80123a7bb1801e7a
b1802a7a14c89f016d74ce331bf080ce9e08f8414f04bca133bfe642fe5e07bb
c4ec0000000049454e44ae426082
"""),
  basn6a08=_dehex("""
89504e470d0a1a0a0000000d4948445200000020000000200806000000737a7a
f40000000467414d41000186a031e8965f0000006f49444154789cedd6310a80
300c46e12764684fa1f73f55048f21c4ddc545781d52e85028fc1f4d28d98a01
305e7b7e9cffba33831d75054703ca06a8f90d58a0074e351e227d805c8254e3
1bb0420f5cdc2e0079208892ffe2a00136a07b4007943c1004d900195036407f
011bf00052201a9c160fb84c0000000049454e44ae426082
"""),
  s09n3p02=_dehex("""
89504e470d0a1a0a0000000d49484452000000090000000902030000009dffee
830000000467414d41000186a031e8965f000000037342495404040477f8b5a3
0000000c504c544500ff000077ffff00ffff7700ff5600640000001f49444154
789c63600002fbff0c0c56ab19182ca381581a4283f82071200000696505c36a
437f230000000049454e44ae426082
"""),
  tbgn3p08=_dehex("""
89504e470d0a1a0a0000000d494844520000002000000020080300000044a48a
c60000000467414d41000186a031e8965f00000207504c54457f7f7fafafafab
abab110000222200737300999999510d00444400959500959595e6e600919191
8d8d8d620d00898989666600b7b700911600000000730d007373736f6f6faaaa
006b6b6b676767c41a00cccc0000f30000ef00d51e0055555567670000dd0051
515100d1004d4d4de61e0038380000b700160d0d00ab00560d00090900009500
009100008d003333332f2f2f2f2b2f2b2b000077007c7c001a05002b27000073
002b2b2b006f00bb1600272727780d002323230055004d4d00cc1e00004d00cc
1a000d00003c09006f6f00002f003811271111110d0d0d55554d090909001100
4d0900050505000d00e2e200000900000500626200a6a6a6a2a2a29e9e9e8484
00fb00fbd5d500801100800d00ea00ea555500a6a600e600e6f7f700e200e233
0500888888d900d9848484c01a007777003c3c05c8c8008080804409007c7c7c
bb00bbaa00aaa600a61e09056262629e009e9a009af322005e5e5e05050000ee
005a5a5adddd00a616008d008d00e20016050027270088110078780000c40078
00787300736f006f44444400aa00c81e004040406600663c3c3c090000550055
1a1a00343434d91e000084004d004d007c004500453c3c00ea1e00222222113c
113300331e1e1efb22001a1a1a004400afaf00270027003c001616161e001e0d
160d2f2f00808000001e00d1d1001100110d000db7b7b7090009050005b3b3b3
6d34c4230000000174524e530040e6d86600000001624b474402660b7c640000
01f249444154789c6360c0048c8c58049100575f215ee92e6161ef109cd2a15e
4b9645ce5d2c8f433aa4c24f3cbd4c98833b2314ab74a186f094b9c2c27571d2
6a2a58e4253c5cda8559057a392363854db4d9d0641973660b0b0bb76bb16656
06970997256877a07a95c75a1804b2fbcd128c80b482a0b0300f8a824276a9a8
ec6e61612b3e57ee06fbf0009619d5fac846ac5c60ed20e754921625a2daadc6
1967e29e97d2239c8aec7e61fdeca9cecebef54eb36c848517164514af16169e
866444b2b0b7b55534c815cc2ec22d89cd1353800a8473100a4485852d924a6a
412adc74e7ad1016ceed043267238c901716f633a812022998a4072267c4af02
92127005c0f811b62830054935ce017b38bf0948cc5c09955f030a24617d9d46
63371fd940b0827931cbfdf4956076ac018b592f72d45594a9b1f307f3261b1a
084bc2ad50018b1900719ba6ba4ca325d0427d3f6161449486f981144cf3100e
2a5f2a1ce8683e4ddf1b64275240c8438d98af0c729bbe07982b8a1c94201dc2
b3174c9820bcc06201585ad81b25b64a2146384e3798290c05ad280a18c0a62e
e898260c07fca80a24c076cc864b777131a00190cdfa3069035eccbc038c30e1
3e88b46d16b6acc5380d6ac202511c392f4b789aa7b0b08718765990111606c2
9e854c38e5191878fbe471e749b0112bb18902008dc473b2b2e8e72700000000
49454e44ae426082
"""),
  # tp2n3p08 is not actually in PngSuite (yet)
  tp2n3p08=_dehex("""
89504e470d0a1a0a0000000d494844520000002000000020080300000044a48a
c60000000467414d41000186a031e8965f00000300504c544502ffff80ff05ff
7f0703ff7f0180ff04ff00ffff06ff000880ff05ff7f07ffff06ff000804ff00
0180ff02ffff03ff7f02ffff80ff0503ff7f0180ffff0008ff7f0704ff00ffff
06ff000802ffffff7f0704ff0003ff7fffff0680ff050180ff04ff000180ffff
0008ffff0603ff7f80ff05ff7f0702ffffff000880ff05ffff0603ff7f02ffff
ff7f070180ff04ff00ffff06ff000880ff050180ffff7f0702ffff04ff0003ff
7fff7f0704ff0003ff7f0180ffffff06ff000880ff0502ffffffff0603ff7fff
7f0702ffff04ff000180ff80ff05ff0008ff7f07ffff0680ff0504ff00ff0008
0180ff03ff7f02ffff02ffffffff0604ff0003ff7f0180ffff000880ff05ff7f
0780ff05ff00080180ff02ffffff7f0703ff7fffff0604ff00ff7f07ff0008ff
ff0680ff0504ff0002ffff0180ff03ff7fff0008ffff0680ff0504ff000180ff
02ffff03ff7fff7f070180ff02ffff04ff00ffff06ff0008ff7f0780ff0503ff
7fffff06ff0008ff7f0780ff0502ffff03ff7f0180ff04ff0002ffffff7f07ff
ff0604ff0003ff7fff00080180ff80ff05ffff0603ff7f0180ffff000804ff00
80ff0502ffffff7f0780ff05ffff0604ff000180ffff000802ffffff7f0703ff
7fff0008ff7f070180ff03ff7f02ffff80ff05ffff0604ff00ff0008ffff0602
ffff0180ff04ff0003ff7f80ff05ff7f070180ff04ff00ff7f0780ff0502ffff
ff000803ff7fffff0602ffffff7f07ffff0680ff05ff000804ff0003ff7f0180
ff02ffff0180ffff7f0703ff7fff000804ff0080ff05ffff0602ffff04ff00ff
ff0603ff7fff7f070180ff80ff05ff000803ff7f0180ffff7f0702ffffff0008
04ff00ffff0680ff0503ff7f0180ff04ff0080ff05ffff06ff000802ffffff7f
0780ff05ff0008ff7f070180ff03ff7f04ff0002ffffffff0604ff00ff7f07ff
000880ff05ffff060180ff02ffff03ff7f80ff05ffff0602ffff0180ff03ff7f
04ff00ff7f07ff00080180ffff000880ff0502ffff04ff00ff7f0703ff7fffff
06ff0008ffff0604ff00ff7f0780ff0502ffff03ff7f0180ffdeb83387000000
f874524e53000000000000000008080808080808081010101010101010181818
1818181818202020202020202029292929292929293131313131313131393939
393939393941414141414141414a4a4a4a4a4a4a4a52525252525252525a5a5a
5a5a5a5a5a62626262626262626a6a6a6a6a6a6a6a73737373737373737b7b7b
7b7b7b7b7b83838383838383838b8b8b8b8b8b8b8b94949494949494949c9c9c
9c9c9c9c9ca4a4a4a4a4a4a4a4acacacacacacacacb4b4b4b4b4b4b4b4bdbdbd
bdbdbdbdbdc5c5c5c5c5c5c5c5cdcdcdcdcdcdcdcdd5d5d5d5d5d5d5d5dedede
dededededee6e6e6e6e6e6e6e6eeeeeeeeeeeeeeeef6f6f6f6f6f6f6f6b98ac5
ca0000012c49444154789c6360e7169150d230b475f7098d4ccc28a96ced9e32
63c1da2d7b8e9fb97af3d1fb8f3f18e8a0808953544a4dd7c4c2c9233c2621bf
b4aab17fdacce5ab36ee3a72eafaad87efbefea68702362e7159652d031b07cf
c0b8a4cce28aa68e89f316aedfb4ffd0b92bf79fbcfcfe931e0a183904e55435
8decdcbcc22292b3caaadb7b27cc5db67af3be63e72fdf78fce2d31f7a2860e5
119356d037b374f10e8a4fc92eaa6fee99347fc9caad7b0f9ebd74f7c1db2fbf
e8a180995f484645dbdccad12f38363dafbcb6a573faeca5ebb6ed3e7ce2c29d
e76fbefda38702063e0149751d537b67ff80e8d4dcc29a86bea97316add9b0e3
c0e96bf79ebdfafc971e0a587885e515f58cad5d7d43a2d2720aeadaba26cf5a
bc62fbcea3272fde7efafac37f3a28000087c0fe101bc2f85f0000000049454e
44ae426082
"""),
  basn6a16=_dehex("""
89504e470d0a1a0a0000000d494844520000002000000020100600000023eaa6
b70000000467414d41000186a031e8965f00000d2249444154789cdd995f6c1c
d775c67ff38fb34b724d2ee55a8e4b04a0ac87049100cab4dbd8c6528902cb4d
10881620592e52d4325ac0905bc98a94025e71fd622cb5065ac98a0c283050c0
728a00b6e542a1d126885cd3298928891d9a0444037e904434951d4b90b84b2f
c9dde1fcebc33977a95555348f411e16dfce9d3b77ee77eebde77ce78c95a669
0ad07c17009a13edd898b87dfb1fcb7d2b4d1bff217f33df80deb1e6267df0ff
c1e6e6dfafdf1f5a7fd30f9aef66b6d546dd355bf02c40662e3307f9725a96c6
744c3031f83782f171c148dbc3bf1774f5dad1e79d6f095a3f54d4fbec5234ef
d9a2f8d73afe4f14f57ef4f42def7b44f19060f06b45bddf1c5534d77fd922be
2973a15a82e648661c6e3240aa3612ead952b604bde57458894f29deaf133bac
13d2766f5227a4a3b8cf08da7adfd6fbd6bd8a4fe9dbb43d35e3dfa3f844fbf8
9119bf4f7144094fb56333abf8a86063ca106f94b3a3b512343765e60082097f
1bb86ba72439a653519b09f5cee1ce61c897d37eedf5553580ae60f4af8af33a
b14fd400b6a0f34535c0434afc0b3a9f07147527a5fa7ca218ff56c74d74dc3f
155cfd3325fc278acf2ae1cb4a539f5f9937c457263b0bd51234c732a300cdd1
cc1840f0aaff54db0e4874ed5a9b5d6d27d4bb36746d80de72baa877ff4b275a
d7895ed1897ea4139b5143fcbb1a62560da1ed9662aaed895ec78a91c18795b8
5e07ab4af8ba128e95e682e0728bf8f2e5ae815a091a53d902ac1920d8e05f06
589de8d8d66680789f4e454fb9d9ec66cd857af796ee2d902fa73fd5bba775a2
153580ae44705ed0d37647d15697cb8f14bfa3e3e8fdf8031d47af571503357c
f30d25acedcbbf135c9a35c49766ba07ab255859e8ec03684e66860182dff8f7
0304bff6ff1c20fc81b7afdd00a71475539a536e36bb5973a19e3b923b02bde5
e4efd4003ac170eb2d13fe274157afedbd82d6fb3a9a1e85e4551d47cf7078f8
9671fe4289ebf5f2bf08d63f37c4eb4773c55a0996efeefa0ca011671d8060ca
2f0004c7fcc300e166ef0240f825efe3361f106d57d423d0723f7acacd66376b
2ed47b7a7a7a205f4ef4ac4691e0aad9aa0d41cf13741c3580a506487574ddca
61a8c403c1863ebfbcac3475168b2de28b8b3d77544bb05ce92a02aceced3c0d
d0cc65ea371b201cf1c601c24dde1c4078cedbdeb60322f50126a019bf6edc9b
39e566b39b3517eaf97c3e0fbde5e4491d45bd74537145d155b476aa0176e868
c6abebf30dbd5e525c54ac8e18e2d56abeb756827a3d970358a97416019a6f64
f60004fdfe1580d5c98e618070cc1b05887eee7e0d209a70db7d8063029889b4
c620ead78d7b33a7dc6c76b3e6427ddddbebde867c393aa7845e5403e8ca794a
d0d6fb897af5f03525fe5782f5e7046bdaef468bf88d1debc6ab25583cd17310
6079b9ab0ba059c914018245bf076075b5a303200c3c1f209a733701444fbbaf
00c4134ebb016c5d0b23614c243701cdf875e3decce9349bddacb9505fbf7dfd
76e82d87736a00f5d2b5ffd4b7dce2719a4d25ae717ee153c1abef18e257cfad
7fa45682da48ef38c052b53b0fd06864b300c151ff08c0ea431de701a287dd5f
004497dc7b01a253ee3e80b8c7f91c20f967fb6fdb7c80ada7d8683723614c24
3701cdf875e3decc29379bddacb950ef3fd47f08f2e5a61ea4aa2a3eb757cd55
13345efcfa59c12b2f19e2578ef77fb75a82854ffbee01a83f977b11a031931d
040802df07082b5e11207cc17b1e209a770700e2df0a83e409fb7580f827c230
99b06fd901fb058d6835dacd481813c94d40337eddb83773cacd66376b2ed437
bebcf165e82d2f4e4beb7f3fa6e652c2d7ee10bc78c010bfb87fe3c95a09ae9f
bd732740bd2fb700d0f865f64180e059ff044018ca0ca28a5b04883f701e0088
bfec7c0c909cb71f0448c6ec518074b375012079d9dedf66004bcfbc51eb2dd1
aadacd481813c94d40337eddb83773cacd66376b2ed487868686205fbe7c49ef
5605a73f34c4a7a787eeab96e0da81bb4e022c15ba27019a5b339300e16bf286
a8eae601e25866907cdf3e0890acb36f00245fb57f05904e59c300e92561946e
b2e600d209ab7d07f04d458dfb46ad1bd16ab49b913026929b8066fcba716fe6
949bcd6ed65ca8ef7e7cf7e3d05b7e7c8f217ee6cdddbb6a25a856f37980e0c7
fe4e80a82623c48193014846ec7180f4acf518409aca0cd28a5504e03b32c374
de1a00608a0240faaa327a4b19fe946fb6f90054dbb5f2333d022db56eb4966a
3723614c243701cdf8f556bea8a7dc6c76b3e66bd46584ddbbcebc0990cf4b0f
ff4070520c282338a7e26700ec725202b01e4bcf0258963c6f1d4d8f0030cb20
805549c520930c03584fa522b676f11600ffc03fde3e1b3489a9c9054c9aa23b
c08856a3dd8c843191dc0434e3d78d7b33a75c36fb993761f7ae5a69f72ef97f
e6ad336fed7e1c60e8bee96980bbdebbb60da07b7069062033d9dc0ae03d296f
70ab511ec071640676252902d833c916007b3e1900b0a6d2028035968e025861
ea01581369fb11488c34d18cbc95989afccca42baad65ba2d5683723614c24d7
8066fcbab8b7e96918baaf5aaa56219f975fb50a43f7c9bde90fa73f1c1a02d8
78f2e27e803b77ca08b90519315b6fe400fc1392097a9eccc0ad444500e70199
a1331f0f00d8934901c07e5d526ceb87c2d07e2579badd005a2b31a5089391b7
1253358049535a6add8856dd0146c298482e01ede27ed878b256ba7600ee3a09
c18fc1df09fe01084ec25defc1b56db0f1a4f4bd78e0e2818d2f0334e7330300
7df7c888b917e50dd9c1c60c80efcb0cbc63e1f700bce7c31700dccbd1060027
8add9b0de06c8e2f00d84962b7d7030e2a61538331b98051f92631bd253f336a
dd8856a3dd44c25c390efddfad96ae9f853b77c25201ba27c533b8bdf28b6ad0
3d084b33d2e7fa59099e9901b8f2d29597fa0f01848f78e70082117f1ca07b76
6910209b9519f895a008d031bbba05c09d8f06005c5b18b8fba25300cea6780e
c03e911c6ccf06d507b48a4fa606634a114609de929f9934c5a87511ad57cfc1
fa476aa5854fa1ef1e3910b905686e85cc24c40138198915f133d2d6dc2a7dea
7df2ccc2a752faf2cec1d577aebeb37e3b4034eeee0008dff3be0e6b923773b4
7904c0ef9119767cb4fa1500ef1361e08e452500f71561e84cc4ed3e20fab6a2
c905f40cb76a3026bf3319b91ac2e46792a6dcd801ebc6aba5da08f48ecb81c8
bd088d5f42f6417191de93908c803d0e76199292b485af41b60e8d9c3c537f0e
8211f0c7211a077707dc18b931b2ee6d80a4d7ae024491ebc24d4a708ff70680
7f25e807e8785f1878e322d6ddaf453f0770ff2dfa769b01423dbbad72a391b6
5a7c3235985629423372494cab55c8f7d64a8b27a0e7202c55a13b0f8d19c80e
4ae9ca3f015115dc3ca467c17a4c7ee95970ab10e5a54ff0ac3cd39881ee5958
1a84f03df0be0e492fd855a8d6aa35d10b4962dbb0a604a3d3ee5e80a8eee600
a24977f8660378bf0bbf00e01d0a8fb7f980f04b8aa6ce6aca8d5a7533c52753
839152c4e222f4dc512dd5eb90cbc981e8ea12cf90cd8a8bf47d89159e2741d3
7124f65b96fcd254dae258fa84a13c13043246a32129574787e49eae2b49b86d
c3e2e78b9ff7f4002415bb08907c66df0d103b4e0c104db90500ff70700c203a
ee1e82dba4c3e16e256c0acca6ceaae9afd1f612d7eb472157ac95962bd05594
7dd1598466053245088e827f44628657942a825b84e4fb601f84b4025611aca3
901e01bb024911dc0a4445f08e41f83df02b10142173149ab71baf027611ea95
7a257704201d14cd9af4d90b00f194530088cb4e09c0df1c5c0088f7393f6833
c0aa3ac156655de3bca9b34ab9716906ba07aba5e5bba1eb3358d90b9da7c533
64f6888bf47b60f521e8380fe10be03d2feac17900927560df40f4e48f805960
50328d648bf4893f9067c217a0631656b7c898c122847bc07b03a2d3e0ee85e4
33b0ef867450c4fad2ecd26cf7168074c0ba0c904cdac300c9cfec4701924df6
1cdca61e10685c6f7d52d0caba1498972f43d740adb4b2009d7d7220b20e3473
90a943d00ffe959bb6eac3e0fe42ea49ee00c45f06e76329b1dabf127d690d80
5581b408f63c2403e0cc433c00ee658836803b0fd100747c04ab5f917704fd10
d5c1cd41ec801343d207f602a403605d86e5f9e5f9ae0d00e994556833806685
c931fb709b0f08b4e869bea5c827859549e82c544b8d29c816a0390999613920
7e610d5727a16318c2003c1fa24be0de2b32caf92224e7c17e5004b6350c4c01
05601218066b0ad28224e149019c086257ca315102de2712903bde97b8144d82
3b2c6ac52d403c054e019249b087f53d0558995a99ea946c70cc927458b3c1ff
550f30050df988d4284376b4566a8e416654cc921985e037e0df0fc131f00f4b
acf0c6211c036f14a239703741740adc7da227edd7e56b833d0ae92549b4d357
25dfb49ed2ff63908e6adf27d6d0dda7638d4154d2778daca17f58e61297c129
41f233b01f5dc3740cac51688c35c6b22580f48224fee9b83502569a66b629f1
09f3713473413e2666e7fe6f6c6efefdfafda1f56f6e06f93496d9d67cb7366a
9964b6f92e64b689196ec6c604646fd3fe4771ff1bf03f65d8ecc3addbb5f300
00000049454e44ae426082
"""),
)

def test_suite(options, args):
    """
    Create a PNG test image and write the file to stdout.
    """

    # Below is a big stack of test image generators.
    # They're all really tiny, so PEP 8 rules are suspended.

    def test_gradient_horizontal_lr(x, y): return x
    def test_gradient_horizontal_rl(x, y): return 1-x
    def test_gradient_vertical_tb(x, y): return y
    def test_gradient_vertical_bt(x, y): return 1-y
    def test_radial_tl(x, y): return max(1-math.sqrt(x*x+y*y), 0.0)
    def test_radial_center(x, y): return test_radial_tl(x-0.5, y-0.5)
    def test_radial_tr(x, y): return test_radial_tl(1-x, y)
    def test_radial_bl(x, y): return test_radial_tl(x, 1-y)
    def test_radial_br(x, y): return test_radial_tl(1-x, 1-y)
    def test_stripe(x, n): return float(int(x*n) & 1)
    def test_stripe_h_2(x, y): return test_stripe(x, 2)
    def test_stripe_h_4(x, y): return test_stripe(x, 4)
    def test_stripe_h_10(x, y): return test_stripe(x, 10)
    def test_stripe_v_2(x, y): return test_stripe(y, 2)
    def test_stripe_v_4(x, y): return test_stripe(y, 4)
    def test_stripe_v_10(x, y): return test_stripe(y, 10)
    def test_stripe_lr_10(x, y): return test_stripe(x+y, 10)
    def test_stripe_rl_10(x, y): return test_stripe(1+x-y, 10)
    def test_checker(x, y, n): return float((int(x*n) & 1) ^ (int(y*n) & 1))
    def test_checker_8(x, y): return test_checker(x, y, 8)
    def test_checker_15(x, y): return test_checker(x, y, 15)
    def test_zero(x, y): return 0
    def test_one(x, y): return 1

    test_patterns = dict(
        GLR=test_gradient_horizontal_lr,
        GRL=test_gradient_horizontal_rl,
        GTB=test_gradient_vertical_tb,
        GBT=test_gradient_vertical_bt,
        RTL=test_radial_tl,
        RTR=test_radial_tr,
        RBL=test_radial_bl,
        RBR=test_radial_br,
        RCTR=test_radial_center,
        HS2=test_stripe_h_2,
        HS4=test_stripe_h_4,
        HS10=test_stripe_h_10,
        VS2=test_stripe_v_2,
        VS4=test_stripe_v_4,
        VS10=test_stripe_v_10,
        LRS=test_stripe_lr_10,
        RLS=test_stripe_rl_10,
        CK8=test_checker_8,
        CK15=test_checker_15,
        ZERO=test_zero,
        ONE=test_one,
        )

    def test_pattern(width, height, bitdepth, pattern):
        """Create a single plane (monochrome) test pattern.  Returns a
        flat row flat pixel array.
        """

        maxval = 2**bitdepth-1
        if maxval > 255:
            a = array('H')
        else:
            a = array('B')
        fw = float(width)
        fh = float(height)
        pfun = test_patterns[pattern]
        for y in range(height):
            fy = float(y)/fh
            for x in range(width):
                a.append(int(round(pfun(float(x)/fw, fy) * maxval)))
        return a

    def test_rgba(size=256, bitdepth=8,
                    red="GTB", green="GLR", blue="RTL", alpha=None):
        """
        Create a test image.  Each channel is generated from the
        specified pattern; any channel apart from red can be set to
        None, which will cause it not to be in the image.  It
        is possible to create all PNG channel types (K, RGB, RGBA, KA),
        as well as non PNG channel types (RGA, and so on).
        """
        i = test_pattern(size, size, bitdepth, red)
        psize = 1
        for channel in (green, blue, alpha):
            if channel:
                c = test_pattern(size, size, bitdepth, channel)
                i = interleave_planes(i, c, psize, 1)
                psize += 1
        return i

    def test_image(name, bitdepth):
        """
        Create a test image by reading an internal copy of the files
        from the PngSuite.  Returned in flat row flat pixel format.
        """

        def flatten():
            """Returns an iterator that flattens scanlines."""

            for scanline in pixels:
                yield tuple(itertools.chain(*scanline))

        if bitdepth != 8:
            raise NotImplementedError("bit depth %d not supported" % bitdepth)

        if name not in _pngsuite:
            raise NotImplementedError("cannot find PngSuite file %s (use -L for a list)" % name)
        r = Reader(bytes=_pngsuite[name])
        r.preamble()
        if r.alpha:
            get = r.asRGBA8
        else:
            get = r.asRGB8
        w,h,pixels,meta = get()
        assert w == h
        return w, array('B', itertools.chain(*flatten())), r.alpha

    # The body of test_suite()
    size = 256
    if options.test_size:
        size = options.test_size
    depth = 8
    if options.test_deep:
        depth = 16
    greyscale=bool(options.test_black)

    kwargs = {}
    if options.test_red:
        kwargs["red"] = options.test_red
    if options.test_green:
        kwargs["green"] = options.test_green
    if options.test_blue:
        kwargs["blue"] = options.test_blue
    if options.test_alpha:
        kwargs["alpha"] = options.test_alpha
    if greyscale:
        if options.test_red or options.test_green or options.test_blue:
            raise ValueError("cannot specify colours (R, G, B) when greyscale image (black channel, K) is specified")
        kwargs["red"] = options.test_black
        kwargs["green"] = None
        kwargs["blue"] = None
    alpha = bool(options.test_alpha)
    if not args:
        pixels = test_rgba(size, depth, **kwargs)
    else:
        size,pixels,alpha = test_image(args[0], depth)

    writer = Writer(size, size,
                    bitdepth=depth,
                    transparent=options.transparent,
                    background=options.background,
                    gamma=options.gamma,
                    greyscale=greyscale,
                    alpha=alpha,
                    compression=options.compression,
                    interlace=options.interlace)
    writer.write_array(sys.stdout, pixels)


def read_pnm_header(infile, supported=('P5','P6')):
    """
    Read a PNM header, returning (format,width,height,maxval).  width
    and height are in pixels.  maxval is synthesized (as 1) for PBM
    images.
    """
    # Generally, see http://netpbm.sourceforge.net/doc/ppm.html

    header = [infile.read(3).rstrip()]
    if header[0] not in supported:
        raise NotImplementedError('file format %s not supported' % header[0])
    # Expected number of tokens in header (3 for P4, 4 for P6)
    expected = 4
    pbm = ('P1', 'P4')
    if header in pbm:
        expected = 3

    # We have to read the rest of the header byte by byte because the
    # final whitespace character (immediately following the MAXVAL in
    # the case of P6) may not be a newline.  Of course all PNM files in
    # the wild use a newline at this point, so it's tempting to use
    # readline; but it would be wrong.
    def getc():
        c = infile.read(1)
        if c == '':
            raise Error('premature EOF reading PNM header')
        return c

    c = getc()
    while True:
        # Skip whitespace that precedes a token.
        while c.isspace():
            c = getc()
        # Skip comments.
        while c == '#':
            while c not in '\n\r':
                c = getc()
        if not c.isdigit():
            raise Error('unexpected character %s found in header' % c)
        # According to the specification it is legal to have comments
        # that appear in the middle of a token.
        # This is bonkers; I've never seen it; and it's a bit awkward to
        # code good lexers in Python (no goto).  So we break on such
        # cases.
        token = ''
        while c.isdigit():
            token += c
            c = getc()
        header.append(token)
        if len(header) == expected:
            break
    # Skip comments (again)
    while c == '#':
        while c not in '\n\r':
            c = getc()
    if not c.isspace():
        raise Error('expected header to end with whitespace, not %s' % c)

    if header[0] in pbm:
        # synthesize a MAXVAL
        header.append(1)
    return header[0], int(header[1]), int(header[2]), int(header[3])

def write_ppm(file, width, height, pixels, maxval=255):
    """Write a PPM file.  Assumes MAXVAL 255 and RGB pixels."""
    file.write('P6 %d %d %d\n' % (width, height, maxval))
    # Samples per line
    spl = 3 * width
    # struct format
    fmt = '>%d' % spl
    if maxval > 0xff:
        fmt = fmt + 'H'
    else:
        fmt = fmt + 'B'
    for l in pixels:
        file.write(struct.pack(fmt, *itertools.chain(*l)))
    file.flush()

def color_triple(color):
    """
    Convert a command line color value to a RGB triple of integers.
    FIXME: Somewhere we need support for greyscale backgrounds etc.
    """
    if color.startswith('#') and len(color) == 4:
        return (int(color[1], 16),
                int(color[2], 16),
                int(color[3], 16))
    if color.startswith('#') and len(color) == 7:
        return (int(color[1:3], 16),
                int(color[3:5], 16),
                int(color[5:7], 16))
    elif color.startswith('#') and len(color) == 13:
        return (int(color[1:5], 16),
                int(color[5:9], 16),
                int(color[9:13], 16))


def _main():
    """
    Run the PNG encoder with options from the command line.
    """
    # Parse command line arguments
    from optparse import OptionParser
    version = '%prog ' + __revision__.strip('$').replace('Rev: ', 'r')
    parser = OptionParser(version=version)
    parser.set_usage("%prog [options] [imagefile]")
    parser.add_option('-r', '--read-png', default=False,
                      action='store_true',
                      help='Read PNG, write PNM')
    parser.add_option("-i", "--interlace",
                      default=False, action="store_true",
                      help="create an interlaced PNG file (Adam7)")
    parser.add_option("-t", "--transparent",
                      action="store", type="string", metavar="color",
                      help="mark the specified color as transparent")
    parser.add_option("-b", "--background",
                      action="store", type="string", metavar="color",
                      help="save the specified background color")
    parser.add_option("-a", "--alpha",
                      action="store", type="string", metavar="pgmfile",
                      help="alpha channel transparency (RGBA)")
    parser.add_option("-g", "--gamma",
                      action="store", type="float", metavar="value",
                      help="save the specified gamma value")
    parser.add_option("-c", "--compression",
                      action="store", type="int", metavar="level",
                      help="zlib compression level (0-9)")
    parser.add_option("-T", "--test",
                      default=False, action="store_true",
                      help="create a test image (a named PngSuite image if an argument is supplied)")
    parser.add_option('-L', '--list',
                      default=False, action='store_true',
                      help="print list of named test images")
    parser.add_option("-R", "--test-red",
                      action="store", type="string", metavar="pattern",
                      help="test pattern for the red image layer")
    parser.add_option("-G", "--test-green",
                      action="store", type="string", metavar="pattern",
                      help="test pattern for the green image layer")
    parser.add_option("-B", "--test-blue",
                      action="store", type="string", metavar="pattern",
                      help="test pattern for the blue image layer")
    parser.add_option("-A", "--test-alpha",
                      action="store", type="string", metavar="pattern",
                      help="test pattern for the alpha image layer")
    parser.add_option("-K", "--test-black",
                      action="store", type="string", metavar="pattern",
                      help="test pattern for greyscale image")
    parser.add_option("-D", "--test-deep",
                      default=False, action="store_true",
                      help="use test patterns with 16 bits per layer")
    parser.add_option("-S", "--test-size",
                      action="store", type="int", metavar="size",
                      help="width and height of the test image")
    (options, args) = parser.parse_args()

    # Convert options
    if options.transparent is not None:
        options.transparent = color_triple(options.transparent)
    if options.background is not None:
        options.background = color_triple(options.background)

    if options.list:
        for name in sorted(_pngsuite):
            print name
        return

    # Run regression tests
    if options.test:
        return test_suite(options, args)

    # Prepare input and output files
    if len(args) == 0:
        infilename = '-'
        infile = sys.stdin
    elif len(args) == 1:
        infilename = args[0]
        infile = open(infilename, 'rb')
    else:
        parser.error("more than one input file")
    outfile = sys.stdout

    if options.read_png:
        # Encode PNG to PPM
        png = Reader(file=infile)
        width,height,pixels,meta = png.asRGB8()
        write_ppm(outfile, width, height, pixels) 
    else:
        # Encode PNM to PNG
        format, width, height, maxval = read_pnm_header(infile, ('P5','P6'))
        greyscale = format == 'P5'
        try:
            mi = [1, 3, 15, 255, 65535].index(maxval)
        except ValueError:
            raise NotImplementedError(
              'maxval %s not supported' % header[3])
        bitdepth = 2**mi
        if bitdepth < 8 and options.alpha:
            raise ValueError('alpha channel not supported with bit depth %d' %
              bitdepth)
        writer = Writer(width, height,
                        greyscale=greyscale,
                        bitdepth=bitdepth,
                        interlace=options.interlace,
                        transparent=options.transparent,
                        background=options.background,
                        alpha=bool(options.alpha),
                        gamma=options.gamma,
                        compression=options.compression)
        if options.alpha:
            pgmfile = open(options.alpha, 'rb')
            format, awidth, aheight, amaxval = read_pnm_header(pgmfile, 'P5')
            if amaxval != '255':
                raise NotImplementedError(
                  'maxval %s not supported for alpha channel' % amaxval)
            if (awidth, aheight) != (width, height):
                raise ValueError("alpha channel image size mismatch"
                                 " (%s has %sx%s but %s has %sx%s)"
                                 % (infilename, width, height,
                                    options.alpha, awidth, aheight))
            writer.convert_ppm_and_pgm(infile, pgmfile, outfile)
        else:
            writer.convert_pnm(infile, outfile)


if __name__ == '__main__':
    _main()
