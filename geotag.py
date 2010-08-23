#!/usr/bin/env python

# GPS + Photos -> Google Earth
# Takes a GPS tracklog in .gpx format, a directory of photos
# and produces a GPX file for Google Earth or whatever
#
# Optionally updates files

# Licence:
# Do whatever you like with this code - except claim that you were the author

# Author:
# Jamie Lawrence, 4th August 2006
# Additions/modifications by Mike Pickering
# Interpolation, Python 3 compliance by Julian Rueth, August 2010

import re, os, tempfile, sys, subprocess, traceback
from argparse import ArgumentParser
from math import pi, sin, cos, atan2, sqrt
from time import strptime, mktime, strftime, gmtime
from gpsfuncs import decToDMS, formatAsRational, Trackpoint
from xml.dom import minidom
from xml.dom.minidom import getDOMImplementation
# PyXML's PrettyPrint looks nicer than toprettyxml; try to import it
try:
    from xml.dom.ext import PrettyPrint
except:
    None


class Photo:
    """A simple holder class for the photo data"""
    def __repr__(self):
        return "[%d,%s,%s,%s]" % (self.filename, self.time, self.trackpoint)

    pass

def getExif(photo):
    """Get the EXIF tags for a file as returned by exiv2.
    """
    stdout = subprocess.Popen(["exiv2","pr",photo.filename],stdout=subprocess.PIPE).communicate()[0];
    return dict( (items[0].strip(),items[1].strip()) for items in [line.split(b':',1) for line in stdout.split(b'\n')] if len(items)==2)

def setExif(photo):
    """Set the EXIF tags on this photo.

       photo is a Photo type containing trackpoint information and the
       filename of the .jpg file to be operated on.

       In addition to writing the GPSInfo EXIF tags, an EXIF comment
       is written with the same information.
    """

    # poor man's ? operator
    latref = ("N", "S")[photo.trackpoint.lat < 0]
    latdeg, latmin, latsec = [formatAsRational(deg) for deg in decToDMS(abs(photo.trackpoint.lat))]
    lonref = ("E", "W")[photo.trackpoint.lon < 0]
    londeg, lonmin, lonsec = [formatAsRational(deg) for deg in decToDMS(abs(photo.trackpoint.lon))]
    altref = (0, 1)[photo.trackpoint.ele < 0]
    alt = formatAsRational(abs(photo.trackpoint.ele))

    subprocess.check_call(["exiv2","-k",
        "-M","set Exif.Photo.UserComment charset=Ascii %s"%photo.trackpoint.getstr(),
        "-M","set Exif.GPSInfo.GPSVersionID 2 2 0 0",
        "-M","set Exif.GPSInfo.GPSLatitudeRef %s"%latref,
        "-M","set Exif.GPSInfo.GPSLatitude %s %s %s"%(latdeg,latmin,latsec),
        "-M","set Exif.GPSInfo.GPSAltitudeRef %s"%altref,
        "-M","set Exif.GPSInfo.GPSAltitude %s"%alt,
        "-M","set Exif.GPSInfo.GPSMapDatum WGS-84",
        "-M","set Exif.GPSInfo.GPSLongitudeRef %s"%lonref,
        "-M","set Exif.GPSInfo.GPSLongitude %s %s %s"%(londeg,lonmin,lonsec),
        photo.filename])

def interpolate_n(deltas, values):
    """For values[0]=f(x0), values[1]=f(x1), do linear interpolation to find f(x) with |x-x0|=deltas[0], |x-x1|=deltas[1].
    """
    weights = []
    for delta in deltas:
       weights.append(delta==0);
    if sum(weights)==0:
        weights = [1/delta for delta in deltas]
    return sum([value*weight for (value,weight) in zip(values,weights)])/sum(weights)

def findNearestTrackpoint(list, time, interpolate, threshold):
    """Search the list of trackpoints, and return the one with the time nearest to time possibly interpolating between closest trackpoints

       Return None if no trackpoint exists within threshold seconds
    """
    closestPoints = [None, None] # the closest point before and after
    closestTimes = [threshold, threshold]  # Python datetimes are seconds
    # iterate over the list (binary search would work too)
    for trackpoint in list:
        delta = trackpoint.time - time
        after = (delta >= 0)
        # if this point is closer than the closest recorded yet
        if abs(delta) < closestTimes[after]:
            closestTimes[after] = abs(delta)
            closestPoints[after] = trackpoint

    for i in [0,1]:
        if closestPoints[i] is None: closestPoints[i] = closestPoints[1-i]
    #if both are None then there had been no points at all and we're screwed

    #reduce the !interpolate case to the interpolate case
    if not interpolate:
        for i in [0,1]:
            if abs(closestPoints[i].time - time)<=abs(closestPoints[1-i].time-time):
                closestPoints[1-i]=closestPoints[i]

    #we use normal vectors for interpolation purposes (http://en.wikipedia.org/wiki/N-vector)
    normals = [ (cos(point.lat/90.*pi)*cos(point.lon/90.*pi),
                cos(point.lat/90.*pi)*sin(point.lon/90.*pi),
                sin(point.lat/90.*pi)) for point in closestPoints ]
    deltas = [ abs(time-point.time) for point in closestPoints ]
    #interpolate the normal vectors
    normal = [ interpolate_n(deltas, values) for values in zip(*normals) ]
    #normalize the result
    normal = [ value/sum(v*v for v in normal) for value in normal ]
    #interpolate the elevation
    elevation = interpolate_n(deltas, (closestPoints[0].ele, closestPoints[1].ele))

    #convert everything back to lat/lon coordinates
    ret = Trackpoint(
            atan2(normal[2],sqrt(normal[0]**2+normal[1]**2))/pi*90,
            atan2(normal[1],normal[0])/pi*90,
            elevation);
    ret.time = time
    return ret

def main():
    # Parse the options
    parser = ArgumentParser()
    parser.add_argument("args", metavar="PHOTO", nargs='*', help='photos to be processed')
    parser.add_argument("-g", "--gps", dest="gps", required=True,
                      help="The input GPS track file in .gpx format", metavar="FILE")
    parser.add_argument("-p", "--photos", dest="photos",
                      help="The directory of photos", metavar="DIR")
    # MPickering added next option; this offset is added to the JPG values (which don't have
    # native timezone information)
    parser.add_argument("-t", "--timediff", dest="timediff", type=int, default=0,
                      help="Add this number of hours to the JPEG times")
    parser.add_argument("-o", "--output", dest="output",
                      help="The output filename for the GPX file", metavar="FILE")
    parser.add_argument("-u", "--update-photos", action="store_true",
                      dest="updatephotos", help="Update the photos with GPS information")
    parser.add_argument("-v", "--verbose",
                      action="store_true", dest="verbose")  # not used; could be useful
    parser.add_argument("-i", "--interpolate", action="store_true", dest="interpolate",
                      help="interpolate coordinates linearily between closest track points")
    parser.add_argument("--threshold", dest="threshold", type=int, default=5*60,
                      help="threshold in seconds that a track point may differ from a photos timestamp still allowing them to get associated; set to -1 to allow arbitrary threshold.")
    options = parser.parse_args()
    args = options.args

    if options.threshold==-1: options.threshold = float("inf")

    # Load and Parse the GPX file to retrieve all the trackpoints
    xmldoc = minidom.parse(options.gps)
    gpx = xmldoc.getElementsByTagName("gpx")
    # get all trackpoints, irrespective of their track
    trackpointElements = gpx[0].getElementsByTagName("trkpt")

    photos = []
    trackpoints = []

    # Iterate over the trackpoints; put them in a list sorted by time
    for pt in trackpointElements:
        timeElement = pt.getElementsByTagName("time")
        time = ""
        if timeElement:
            timeString = timeElement[0].firstChild.data
            # times are in xsd:dateTime:  <time>2006-12-20T15:01:06Z</time>
            time = mktime(strptime(timeString[0:len(timeString)-1], "%Y-%m-%dT%H:%M:%S"))
            trackpoint = Trackpoint()
            trackpoint.lat = float(pt.attributes["lat"].value)
            trackpoint.lon = float(pt.attributes["lon"].value)
            trackpoint.ele = float(pt.getElementsByTagName("ele")[0].firstChild.data)
            trackpoint.time = time
            trackpoints.append(trackpoint)
    trackpoints.sort(key=lambda obj:obj.time)

    # prepare the list of photos
    if options.photos:
        photolist = filter(lambda x: os.path.isfile(x) and               \
          os.path.splitext(x)[1].lower() == '.jpg',                      \
          [os.path.join(options.photos, photo) for photo in os.listdir(options.photos)])
    else:
        photolist = args
    photolist.sort()

    for file in photolist:
        photo = Photo()
        photo.filename = file
        photo.shortfilename = os.path.split(file)[1]
        # Parse the EXIF data and find the closest matching trackpoint
        tags = getExif(photo)
        try:
            photo.time = mktime(strptime(bytes.decode(tags[b'Image timestamp']), "%Y:%m:%d %H:%M:%S"))
            # account for time difference (GPX uses UTC; EXIF uses local time)
            photo.time += options.timediff * 3600
            photo.trackpoint = findNearestTrackpoint(trackpoints, photo.time, options.interpolate, options.threshold)
            if photo.trackpoint:
                photos.append(photo)
        except:
            # picture may have been unreadable, may not have had timestamp, etc.
            print(photo.filename, traceback.format_exc())


    # ready to output the photo listing
    impl = getDOMImplementation()
    gpxdoc = impl.createDocument(None, "gpx", None)
    doc_element = gpxdoc.documentElement

    # <gpx> (top-level) element attributes
    doc_element.setAttribute("version", "1.0")
    doc_element.setAttribute("creator", "gpspoint_to_gpx.py")
    doc_element.setAttribute("xmlns:xsi", "http://www.w3.org/2001/XMLSchema-instance")
    doc_element.setAttribute("xmlns", "http://www.topografix.com/GPX/1/0")
    doc_element.setAttribute("xsi:schemaLocation",   \
      "http://www.topografix.com/GPX/1/0 http://www.topografix.com/GPX/1/0/gpx.xsd")

    # <gpx> contains <time> element
    time_element = gpxdoc.createElement("time")
    time_element.appendChild(gpxdoc.createTextNode(   \
                                strftime("%Y-%m-%dT%H:%M:%SZ", gmtime())))
    doc_element.appendChild(time_element)

    # <bounds> element needs to go next; we'll fill in the attributes later
    bounds_element = gpxdoc.createElement("bounds")
    doc_element.appendChild(bounds_element)

    # header is complete; now need to iterate over the photomap dictionary
    # and add waypoints
    minlat = 90.0
    maxlat = -90.0
    minlon = 180.0
    maxlon = -180.0

    for photo in photos:
        lat = photo.trackpoint.lat
        lon = photo.trackpoint.lon
        ele = photo.trackpoint.ele

        # track minimum and maximum for the <bounds> element
        if (lat < minlat):
            minlat = lat
        if (lat > maxlat):
            maxlat = lat
        if (lon < minlon):
            minlon = lon
        if (lon > maxlon):
            maxlon = lon

        wpt_element = gpxdoc.createElement("wpt")
        doc_element.appendChild(wpt_element)

        wpt_element.setAttribute("lat", str(lat))
        wpt_element.setAttribute("lon", str(lon))

        ele_element = gpxdoc.createElement("ele")
        wpt_element.appendChild(ele_element)
        ele_element.appendChild(gpxdoc.createTextNode(str(ele)))

        name_element = gpxdoc.createElement("name")
        wpt_element.appendChild(name_element)
        name_element.appendChild(gpxdoc.createTextNode(photo.shortfilename))

        cmt_element = gpxdoc.createElement("cmt")
        wpt_element.appendChild(cmt_element)
        cmt_element.appendChild(gpxdoc.createTextNode(photo.shortfilename))

        # use filename as the description
        # we could check the photo comment, if it exists, and use that...
        desc_element = gpxdoc.createElement("desc")
        wpt_element.appendChild(desc_element)
        desc_element.appendChild(gpxdoc.createTextNode(photo.shortfilename))

        # now, assemble and execute the exiv2 command
        if options.updatephotos:
            setExif(photo);

    # finish the bounds element
    bounds_element.setAttribute("minlat", str(minlat))
    bounds_element.setAttribute("minlon", str(minlon))
    bounds_element.setAttribute("maxlat", str(maxlat))
    bounds_element.setAttribute("maxlon", str(maxlon))

    # dump the document
    if options.output:
        outfile = open(options.output, "w")
    else:
        outfile = sys.stdout

    try:
        PrettyPrint(gpxdoc, outfile)
    except:
        outfile.write(gpxdoc.toprettyxml("  "))

    outfile.close()


if __name__ == "__main__":
    main()
