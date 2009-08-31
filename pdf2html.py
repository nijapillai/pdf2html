#!/usr/bin/python
"""
Converts PDF to HTML e-books.

Relies on pdftohtml (http://pdftohtml.sourceforge.net/).  Requires Python 2.5
or later.

Method of operation:

    1. Run pdftohtml -xml on the PDF file
    2. Process the XML file, detect paragraph boundaries by paying careful
       attention to first-line indents
    3. Produce an HTML

The HTML produced differs from the one you'd get from pdftohtml in these ways:

    * Paragraphs are preserved; line breaks inside paragraphs are lost.
    * Multiple adjacent spaces are left as spaces, not converted to a run of
      &nbsp;
    * Page boundaries are discarded, not rendered as <hr>
    * The HTML produced is modern XHTML, not ancient HTML 3 with an explicit
      dark-grey bgcolor on the <BODY>.

Bugs:

    * doesn't handle superscript well
    * loses information such as fonts and colours

Copyright (c) 2009 Marius Gedminas <marius@gedmin.as>.
Licenced under the GNU GPL.
"""

import optparse
import os
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from xml.etree import cElementTree as ET


__version__ = '0.1'
__author__ = 'Marius Gedminas'


class Error(Exception):
    pass


def convert_pdf_to_html(pdf_file, html_file):
    tmpdir = tempfile.mkdtemp('pdf2html')
    try:
        xml_file = os.path.join(tmpdir, 'data') # pdf2html always adds .xml
        subprocess.check_call(['pdftohtml', '-xml', pdf_file, xml_file])
        xml_file += '.xml'
        convert_pdfxml_to_html(xml_file, html_file)
    finally:
        shutil.rmtree(tmpdir)


def convert_pdfxml_to_html(xml_file, html_file):
    # The structure of the pdf2xml documents is this:
    #   <pdf2xml>
    #     <page number="1" position="absolute" top="0" left="0"
    #           height="800" width="600" >
    #       <fontspec id="0" size="12" family="Times" color="#000000" />
    #       ...
    #       <text top="100" left="60" width="200" height="13"
    #             font="0"><i><b>Some text</b></i></text>
    #       ...
    #     </page>
    #     ...
    #   </pdf2xml>
    # Notes:
    #   * The scope of <fontspecs> is larger than a single page
    #   * Coordinates are typical screen coordinates: i.e. (0, 0) is top-left
    #     and y increases downwards
    tree = ET.parse(xml_file)
    root_tag = tree.getroot().tag
    if root_tag != 'pdf2xml':
        raise Error('Expected a pdf2xml document, got %s' % root_tag)

    html = ET.Element('html')
    html.text = html.tail = '\n'
    head = ET.SubElement(html, 'head')
    head.text = head.tail = '\n'
    charset = ET.SubElement(head, 'meta',
                            {'http-equiv': 'content-type',
                             'content': 'text/html; charset=UTF-8'})
    charset.tail = '\n'
    generator = ET.SubElement(head, 'meta',
                              name='generator',
                              content='pdf2html %s by %s' % (__version__,
                                                             __author__))
    generator.tail = '\n'
    title = ET.SubElement(head, 'title')
    title.text = html_file
    title.tail = '\n'
    body = ET.SubElement(html, 'body')
    body.text = body.tail = '\n'

    class Font(object):
        def __init__(self, size, family, color):
            self.size = size
            self.family = family
            self.color = color

        def __hash__(self):
            return hash(self.size, self.family, self.color)

        def __eq__(self, other):
            return type(other) == type(self) and (self.size, self.family,
                                                  self.color) == (other.size,
                                                  other.family, other.color)

        def __ne__(self, other):
            return not self.__eq__(other)

    fonts = {}
    for page in tree.findall('page'):
        for fontspec in page.findall('fontspec'):
            font = Font(fontspec.get('size'), fontspec.get('family'),
                        fontspec.get('color'))
            fonts[fontspec.get('id')] = font

    def iter_attrs(attr):
        frequencies = defaultdict(int)
        for page in tree.findall('page'):
            for chunk in page.findall('text'):
                yield chunk.get(attr)

    def count_frequencies(attr):
        frequencies = defaultdict(int)
        for value in iter_attrs(attr):
            frequencies[value] += 1
        return frequencies

    def n_most_frequent(attr, n):
        frequencies = count_frequencies(attr)
        frequencies = [(freq, value) for value, freq in frequencies.items()]
        frequencies.sort()
        if False: # debug
            print attr + ':'
            for f, v in frequencies[-5:]:
                print f, v
        return [value for (freq, value) in frequencies[-n:]]

    def most_frequent(attr):
        values = n_most_frequent(attr, 1)
        if values:
            return values[0]
        else:
            return object() # something not equal to anything else

    most_frequent_height = most_frequent('height')
    most_frequent_font = fonts[most_frequent('font')]
        # XXX sometimes you have more than one fontspec with the same
        # attributes (family, size, color), this might skew the frequency
        # distribution somewhat

    # XXX: could crash if there are no text chunks at all
    text_width = max(int(w) for w in iter_attrs('width')) * 9 / 10

    # XXX could crash if there are no text chunks or all of them are at the
    # same x position
    left_pos, indent = sorted(map(int, n_most_frequent('left', 2)))

    def looks_like_a_heading(chunk):
        if len(chunk) != 1:
            return False
        bold = chunk
        while bold.tag != 'b':
            if len(bold) != 1:
                return False
            bold = bold[0]
        return (fonts[chunk.get('font')] != most_frequent_font
                and int(chunk.get('height')) >= int(most_frequent_height)
                and bold.text
                and any(c.isalpha() for c in bold.text))

    para = None
    prev_chunk = None
    for page in tree.findall('page'):
        for chunk in page.findall('text'):
            if prev_chunk is None:
                continues_paragraph = False
            else:
                sanity_limit = (int(prev_chunk.get('top'))
                                + int(prev_chunk.get('height'))
                                + int(fonts[prev_chunk.get('font')].size) / 2)
                continues_paragraph = (
                    int(chunk.get('left')) != indent and
                    int(chunk.get('left')) <= int(prev_chunk.get('left')) and
                    int(prev_chunk.get('width')) >= text_width and
                    int(chunk.get('top')) <= sanity_limit and
                    fonts[chunk.get('font')] == fonts[prev_chunk.get('font')]
                ) or (
                    chunk.get('top') == prev_chunk.get('top')
                )

            if para is not None and continues_paragraph:
                # join with previous
                if chunk.text is None:
                    chunk.text = ''
                if len(para):
                    if para[-1].tail:
                        para[-1].tail += '\n' + chunk.text
                    else:
                        para[-1].tail = '\n' + chunk.text
                else:
                    if para.text:
                        para.text += '\n' + chunk.text
                    else:
                        para.text = chunk.text
                para[len(para):] = chunk[:]
            else:
                # start new paragraph
                if looks_like_a_heading(chunk):
                    para = ET.SubElement(body, 'h2')
                else:
                    para = ET.SubElement(body, 'p')
                para.text = chunk.text
                para[:] = chunk[:]
                para.tail = '\n'
            prev_chunk = chunk

    file(html_file, 'w').write(ET.tostring(html))


def main():
    parser = optparse.OptionParser(usage='%prog input.pdf [output.html]')
    opts, args = parser.parse_args()
    if len(args) < 1:
        parser.error('please specify an input file name')
    if len(args) > 2:
        parser.error('too many arguments')

    pdf_name = args[0]
    if len(args) > 1:
        output_name = args[1]
    else:
        output_name = os.path.splitext(pdf_name)[0] + '.html'

    try:
        if os.path.splitext(pdf_name)[1] == '.xml':
            convert_pdfxml_to_html(pdf_name, output_name)
        else:
            convert_pdf_to_html(pdf_name, output_name)
    except Error, e:
        sys.exit(str(e))


if __name__ == '__main__':
    main()
