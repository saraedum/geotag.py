#!/usr/bin/env python
#
# A few classes and functions for handling GPS data and photos
#

class Trackpoint:
    """A simple holder class for the trackpoint data"""
    def __init__(self, lat = 0.0, lon=0.0, ele=0.0):
        self.lat = lat
        self.lon = lon
        self.ele = ele

    def __repr__(self): 
        return "[%d,%s,%s,%s]" % (self.time, str(self.lon), str(self.lat), str(self.ele))

    def getstr(self): 
        return "Lat: %f Lon: %f Alt: %dm" % (self.lat, self.lon, self.ele)

    pass


def decToDMS(degrees):
    """Convert a decimal degree measurement into degrees, minutes, and seconds
       Works on positive degrees only!  Handle E/W outside this function!
    """

    work = float(degrees)
    deg = int(work)

    # this puts minutes into work
    work = (work - deg) * 60
    min = int(work)

    # this puts seconds into work
    sec = (work - min) * 60

    return (deg, min, sec)


def dmsToDec(deg, min, sec):
    """Convert a measurement in degrees, minutes, and seconds to decimal
       degrees
    """
    return deg + (float((min * 60) + sec) / 3600)


def formatAsRational(number):
    """Format a floating point number as a rational number
       ex. 10.463 -> 10463/1000
    """
    if number == 0:
       return "0/1"

    stringrep = ('%f' % number)
    (int_portion, frac_portion) = stringrep.rstrip('0').split('.')
    return '%s%s/%d' % (int_portion.lstrip('0'), frac_portion, pow(10,len(frac_portion)))

