# Copyright (C) 2003-2007  Robey Pointer <robeypointer@gmail.com>
#
# This file is part of paramiko.
#
# Paramiko is free software; you can redistribute it and/or modify it under the
# terms of the GNU Lesser General Public License as published by the Free
# Software Foundation; either version 2.1 of the License, or (at your option)
# any later version.
#
# Paramiko is distrubuted in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR
# A PARTICULAR PURPOSE.  See the GNU Lesser General Public License for more
# details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with Paramiko; if not, write to the Free Software Foundation, Inc.,
# 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA.

def byt(x):
    """
    The bytes analog of the byt() function.
    Returns a bytes of length 1 holding the value x.
    """
    return bytes((x,))

# for Python 2
#byt=chr


def safeord(x):
    """
    An ord() function that passes integers through unmodified.
    
    This help manage the differences between element accesses on bytes between Python 2 and 3.
    (Python 3 produces int, Python 2 produces str)
    """
    if isinstance(x, int):
        return x
    else:
        return ord(x)
