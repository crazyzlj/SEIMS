#! /usr/bin/env python
# -*- coding: utf-8 -*-
"""Global variables and utility functions
    @author   : Liangjun Zhu, Junzhi Liu
    @changelog: 16-06-16  first implementation version
                17-06-22  reformat according to pylint and google style
                18-02-08  lj - compatible with Python3.\n
"""
from __future__ import absolute_import

import os
import sys

if os.path.abspath(os.path.join(sys.path[0], '..')) not in sys.path:
    sys.path.insert(0, os.path.abspath(os.path.join(sys.path[0], '..')))

from pygeoc.utils import StringClass, UtilClass

# Global variables
UTIL_ZERO = 1.e-6
MINI_SLOPE = 0.0001
DEFAULT_NODATA = -9999.
SQ2 = 1.4142135623730951
PI = 3.141592653589793


# LFs = ['\r', '\n', '\r\n']


def status_output(status_msg, percent, file_name):
    """Print status and flush to file.
    Args:
        status_msg: status message
        percent: percentage rate of progress
        file_name: file handler
    """
    UtilClass.writelog(file_name, "[Output] %d..., %s" % (percent, status_msg), 'a')


def read_data_items_from_txt(txt_file):
    """Read data items include title from text file, each data element are split by TAB or COMMA.
       Be aware, the separator for each line can only be TAB or COMMA, and COMMA is the recommended.
    Args:
        txt_file: full path of text data file
    Returns:
        2D data array
    """
    data_items = list()
    with open(txt_file, 'r') as f:
        for line in f:
            str_line = line.strip()
            if str_line != '' and str_line.find('#') < 0:
                line_list = StringClass.split_string(str_line, ['\t'])
                if len(line_list) <= 1:
                    line_list = StringClass.split_string(str_line, [','])
                data_items.append(line_list)
    return data_items
