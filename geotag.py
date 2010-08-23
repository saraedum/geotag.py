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

import re, os, tempfile, sys, subprocess, traceback
from optparse import OptionParser
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

def getExiv2Cmd(photo):
    """Get the command string to run exiv2 on the given photo

       photo is a Photo type containing trackpoint information and the
       filename of the .jpg file to be operated on.

       The return value is a string that can be run at the command line
       (or through os.system()) to modify the .jpg file.  In addition to
       writing the GPSInfo EXIF tags, an EXIF comment is written with the
       same information.  Testing has shown that writing a JPEG comment here
       renders the .jpg file unparsable by the Python EXIF module.  If needed,
       it could be done with wrjpgcom.
    """

    # poor man's ? operator
    latref = ("N", "S")[photo.trackpoint.lat < 0]
    latdeg, latmin, latsec = [formatAsRational(deg) for deg in decToDMS(abs(photo.trackpoint.lat))]
    lonref = ("E", "W")[photo.trackpoint.lon < 0]
    londeg, lonmin, lonsec = [formatAsRational(deg) for deg in decToDMS(abs(photo.trackpoint.lon))]
    altref = (0, 1)[photo.trackpoint.ele < 0]
    alt = formatAsRational(abs(photo.trackpoint.ele))

    return """exiv2 -k                                                     \
    -M "set Exif.Photo.UserComment charset=Ascii %(trackpointstr)s"        \
    -M "set Exif.GPSInfo.GPSVersionID 2 2 0 0"                             \
    -M "set Exif.GPSInfo.GPSLatitudeRef %(latref)s"                        \
    -M "set Exif.GPSInfo.GPSLatitude %(latdeg)s %(latmin)s %(latsec)s"     \
    -M "set Exif.GPSInfo.GPSAltitudeRef %(altref)s"                        \
    -M "set Exif.GPSInfo.GPSAltitude %(alt)s"                              \
    -M "set Exif.GPSInfo.GPSMapDatum WGS-84"                               \
    -M "set Exif.GPSInfo.GPSLongitudeRef %(lonref)s"                       \
    -M "set Exif.GPSInfo.GPSLongitude %(londeg)s %(lonmin)s %(lonsec)s"    \
     %(filename)s""" % \
     {"trackpointstr": photo.trackpoint.getstr(),
      "latref": latref, "latdeg": latdeg, "latmin": latmin, "latsec": latsec,
      "lonref": lonref, "londeg": londeg, "lonmin": lonmin, "lonsec": lonsec,
      "altref": altref, "alt": alt, "filename": photo.filename}


def findNearestTrackpoint(list, time):
    """Search the list of trackpoints, and return the one with the time nearest
       to time

       Return None if no trackpoint exists within 5 minutes
       TODO make this time configurable
       TODO give an option to interpolate
    """
    closestTime = 5 * 60  # Python datetimes are seconds
    closestPoint = None
    # iterate over the list (binary search would work too)
    for trackpoint in list:
        # if this point is closer than the closest recorded yet
        if abs(trackpoint.time - time) < closestTime:
            closestTime = abs(trackpoint.time - time)
            closestPoint = trackpoint

        # bail out early.  the trackpoint list is in chronological order...
        # if we've passed the photo's time in the track, may as well exit now
        if trackpoint.time > time:
            break

    return closestPoint


def main():
    # Parse the options
    usage = "usage: %prog [options] [photo1 photo2 ...]"
    parser = OptionParser(usage)
    parser.add_option("-g", "--gps", dest="gps",
                      help="The input GPS track file in .gpx format", metavar="FILE")
    parser.add_option("-p", "--photos", dest="photos",
                      help="The directory of photos", metavar="DIR")
    # MPickering added next option; this offset is added to the JPG values (which don't have
    # native timezone information)
    parser.add_option("-t", "--timediff", dest="timediff", type="int", default=0,
                      help="Add this number of hours to the JPEG times")
    parser.add_option("-o", "--output", dest="output",
                      help="The output filename for the GPX file", metavar="FILE")
    parser.add_option("-u", "--update-photos", action="store_true",
                      dest="updatephotos", help="Update the photos with GPS information")
    parser.add_option("-v", "--verbose",
                      action="store_true", dest="verbose")  # not used; could be useful
    (options, args) = parser.parse_args()

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
            photo.trackpoint = findNearestTrackpoint(trackpoints, photo.time)
            if photo.trackpoint:
                photos.append(photo)
        except e:
            # picture may have been unreadable, may not have had timestamp, etc.
            print photo.filename, e.value


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
            os.system(getExiv2Cmd(photo))

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
