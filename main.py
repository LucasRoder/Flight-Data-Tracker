

import csv
import math
import os
import select
import sys
import time
import objc
import requests
from CoreLocation import CLLocationManager
from Foundation import NSObject, NSRunLoop, NSDate


# How often the tracker refreshes on its own, in seconds, if you don't
# press Enter first. At 22, that's 86400 / 22 = 3927 refreshes/day if
# left running non-stop  under OpenSky's 4,000/day limit
REFRESH_SECONDS = 22


# controls how large an area around that your IP to search.
RADIUS_KM = 5

#opensky api key
CLIENT_ID = ""  # add your OpenSky client ID
CLIENT_SECRET = ""  # add your OpenSky client secret


#This class waits for apples apples location finding service to either send the current location or an error message 
class _LocationDelegate(NSObject):
# internal helper class that inherts from NSObject which is what everything in apples frameworks are built on
# must inhert from because Corelocation is an apple framework

    def init(self):
        # jumps up and runs  NSObject constructor 
        self = objc.super(_LocationDelegate, self).init()

        # plasce holder values for location and error
        self.location = None
        self.error = None
        return self

    # coreloaction calls this function when apples location finding softywrae figures out where user is 
    def locationManager_didUpdateLocations_(self, manager, locations):
        # core location gives a list of locations oldest to newest then -1 gives newest and 
        # most acurate location status from list of locations 
        self.location = locations[-1]

    # coreloaction calls this function when apples location finding softywrae hits an error with figuring 
    # location (location sevrive turned off, no reception, e.g.)
    def locationManager_didFailWithError_(self, manager, error):
        self.error = error

# creates a location searcher (manager) and reciver (delegate) for the manager to report too. the program then
# loops for up to 15 seconds giving (manager) coreloaction a changce to call back the reciver (delegate) and either
# report an error or actuall location

def get_current_location():

    # creates the corelocation getter
    manager = CLLocationManager.alloc().init()

    # creates the location delegate object
    delegate = _LocationDelegate.alloc().init()

    # ties manager to delegate object
    manager.setDelegate_(delegate)

    # tells corelocation to search for location
    manager.startUpdatingLocation()

    # gives us a deadline 15 seconds from now 
    deadline = time.time() + 15

    # keep searching while location or error is not filled
    while delegate.location is None and delegate.error is None:

        # keep searching for 15 seconds then give up
        if time.time() > deadline:
            print("Timed out waiting for your location. Did you allow location access?")
            manager.stopUpdatingLocation()
            return None

        # gives CoreLocation up to 0.2 seconds to actually call one of our
        # callback methods, if it has either location or error ready
        NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.2))
    # tells corelocation to stop searching for location
    manager.stopUpdatingLocation()

    # if an error was flagged display the error 
    if delegate.error is not None:
        print(f"Could not get your location: {delegate.error}")
        return None

    #pulls longitude and latitude and returns it
    coordinate = delegate.location.coordinate()
    return coordinate.latitude, coordinate.longitude


# returns the bounding latitude and logitude box too look for plans based on your cuurent location and 
# search area 
def bounding_box_around(lat, lon, radius_km):
    # takes in currnet lat lon and search radius

    lat_offset = radius_km / 111 # one degre of lat = 111 km
    lon_offset = radius_km / (111 * math.cos(math.radians(lat)))

    lamin = lat - lat_offset
    lamax = lat + lat_offset
    lomin = lon - lon_offset
    lomax = lon + lon_offset
    return lamin, lomin, lamax, lomax


# method takes in bounding bhox and api key sends them to opensky and returns list of flights or none if there are not any
def get_flights(lamin, lomin, lamax, lomax, access_token):

    # openskys live flight data
    url = "https://opensky-network.org/api/states/all"

    # dictonary containg search area 
    params = {
        "lamin": lamin,
        "lomin": lomin,
        "lamax": lamax,
        "lomax": lomax,
    }

    # Attach your login token to opensky request if we have one. With acsses token api key daily limit is 4,000 requests
    # without only 400
    if access_token:
        headers = {"Authorization": f"Bearer {access_token}"}
    else:
        headers = {}

    try:
        # sends bounding box and acsses token to opensky api timeout=10 means: if OpenSky doesn't respond within 10 seconds,
        # give up instead of freezing the program forever.
        response = requests.get(url, params=params, headers=headers, timeout=10)
    except requests.exceptions.RequestException as error:
        #  catches error "no internet connection".
        print(f"Could not reach OpenSky: {error}")
        return []

    if response.status_code == 429:
        # 429 eror means user used up today's credits for this endpoint.
        # OpenSky tells us exactly how long until it resets. (we convert to hours and minutes we must wait)
        retry_seconds = response.headers.get("X-Rate-Limit-Retry-After-Seconds")
        if retry_seconds:
            hours = int(retry_seconds) // 3600
            minutes = (int(retry_seconds) % 3600) // 60
            print(f"Daily rate limit reached. Try again in about {hours}h {minutes}m.")
        else:
            print("Daily rate limit reached. Try again later.")
        return []

    if response.status_code != 200:
        # if any other error occurs throws up this error message 
        print(f"OpenSky returned an error (status code {response.status_code}).")
        return []

    data = response.json()  # turns the response text into a Python dictonary so we can use the data

    # returns a list of flights or none if no flights 
    return data.get("states") or []

# logs you into your opensky account and gives you anacsses token to prove ypou are logged in 
def get_access_token():
    #if clinet ID or secret is empty returns None
    if not CLIENT_ID or not CLIENT_SECRET:
        return None

    #openskys login server
    token_url = (
        "https://auth.opensky-network.org/auth/realms/"
        "opensky-network/protocol/openid-connect/token"
    )

    # login form we are suubmiting to Opensky
    login_info = {
        "grant_type": "client_credentials",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    }

    try:

        response = requests.post(token_url, data=login_info, timeout=10)
        #sends login_info to server 
        response.raise_for_status()  # raises an error if the login failed
    except requests.exceptions.RequestException as error:
        # catches error if flagged
        print(f"Could not log in to OpenSky: {error}")
        return None

    # returns acsses token we use get flights and deperature airport 
    token_info = response.json()
    return token_info["access_token"]


AIRCRAFT_DB_FILE = os.path.join(os.path.dirname(__file__), "aircraft_database.csv") # aircarft database file location/name

# parses aircarft databse CSV and build a dictonary storing (icao24) aircraft unique identifier as the key and the (model + manufacturer)
# and the (airline/operation) as dictonary values
def load_aircraft_database():
    # reads databse file which is all planes registered last updated november 2024
    
    aircraft_db = {} # dictonary holds aircraft type, airline 

    if not os.path.exists(AIRCRAFT_DB_FILE):# if data base file not found return empty dictonary 
        print(f"(Aircraft database not found at {AIRCRAFT_DB_FILE} -- type/airline will show as Unknown.)")
        return aircraft_db

    with open(AIRCRAFT_DB_FILE, newline="", encoding="utf-8") as csv_file:
        reader = csv.reader(csv_file)      # opens csv file and creates reader 
   
        next(reader)  # skip the header row (column names)

        for row in reader: # loops through csv file around 520,000 aircrafts
            icao24 = row[0] #saves unique idetifier number 
            manufacturer = row[3] # aircraft manufacturer
            model = row[4] # plane model
            operator = row[9] # company operating aircraft

            aircraft_type = f"{manufacturer} {model}".strip() or "Unknown" # combines aircraft and manufacturer etc. Boeing 747
            airline = operator.strip() or "Unknown"  # stores aircraft opeartor or unkown if none
            aircraft_db[icao24] = (aircraft_type, airline) # stores idetifier num as key and aircraft type and operator as touple values

    return aircraft_db

# takes lits of current flights in region gcreates readble summaray block and matchs it with aircraft databse based on 
# icao24
def show_flights(flights, aircraft_db):
    # takes in aircraft database and flight in area 

    if not flights: # if no flights return nothing
        return

    print(f"\n--- Flights currently near your location ({len(flights)} found) ---") # banner showing num of flight near location

    for index, flight in enumerate(flights, start=1):
        # loops througfh each flight in flight list and displays 
        icao24 = flight[0] # aircraft id number
        callsign = flight[1] # aircrfat calssign
        origin_country = flight[2] # aircraft origin country
        longitude = flight[5] # currrent longitude
        latitude = flight[6] # currrent latitude
        velocity = flight[9] # currrent speed

        # OpenSky pads callsigns with extra spaces, and sometimes leaves
        # it blank entirely -- clean that up so it prints nicely.
        if callsign:
            callsign = callsign.strip()
        else:
            callsign = "N/A"

        # Position can be missing if OpenSky hasn't received a location for this plane recently.
        if latitude is not None and longitude is not None:
            position = f"{latitude}, {longitude}"
        else:
            position = "N/A"

        # OpenSky gives speed in meters per second -- convert to km/h,
        # which is easier to picture.
        if velocity is not None:
            speed_kmh = velocity * 3.6
            speed = f"{speed_kmh:.0f} km/h"
        else:
            speed = "N/A"

        # Look up aircraft type/airline from the local database (STEP 1c).
        aircraft_type, airline = aircraft_db.get(icao24, ("Unknown", "Unknown"))
        # trys to find aircraft in db if not retrurn unknown, unkown

        # flight info output
        print(f"\nFlight #{index}")
        print(f"  Call sign:      {callsign}")
        print(f"  Aircraft ID:    {icao24}")
        print(f"  Aircraft type:  {aircraft_type}")
        print(f"  Airline:        {airline}")
        print(f"  Origin country: {origin_country or 'N/A'}")
        print(f"  Position:       {position}")
        print(f"  Speed:          {speed}")

# creates refresh banner and waits for timer to run out or user to select enter
def wait_for_next_refresh():

    print(
        f"\nAuto-refreshing in {REFRESH_SECONDS}s "
        "(press Enter to refresh now, or type 'q' then Enter to quit)..."
    ) # refresh banner 

    ready_to_read, _, _ = select.select([sys.stdin], [], [], REFRESH_SECONDS)
    # waits for enter or refresh timer

    if ready_to_read:
        return sys.stdin.readline().strip().lower() #sees if user typed q to quit

    return ""  # timer ran out -- treat it the same as an empty Enter press

# runs/ builds program
def main():
    print("=== Flight Data Tracker ===")
    print("Live flight data provided by the OpenSky Network (opensky-network.org)")
    # intro banner

    print("\nDetecting your current location...")
    location = get_current_location() # gets current location (lan, lon)
    if location is None:
        return  # Can't continue without knowing where to search.

    lat, lon = location
    print(f"Location: {lat}, {lon}") # shows current location 
    # creates bounding box
    lamin, lomin, lamax, lomax = bounding_box_around(lat, lon, RADIUS_KM)

    # loads and saves aircarft databse in memory 
    # Icao24 = (model name, manufacturer, operator)
    print("Loading aircraft database...")
    aircraft_db = load_aircraft_database()

    while True:
        # A fresh token is needed each loop since they expire after 30 minutes.
        access_token = get_access_token()

        # gets flights in box
        flights = get_flights(lamin, lomin, lamax, lomax, access_token)

        # prints readble block of flighst
        show_flights(flights, aircraft_db)

        #gets refresh
        user_input = wait_for_next_refresh()

        # quits if q
        if user_input == "q":
            print("Goodbye!")
            break


if __name__ == "__main__":
    main()
