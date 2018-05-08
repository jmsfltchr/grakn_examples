import json
import grakn
import datetime as dt
import os
import tube_network_example.settings as settings
from utils.utils import check_response_length, match_get, insert, match_insert, get_match_id


def import_query_generator(perform_query, timetables_dir_path):
    """
    Builds the Grakl statements required to import the transportation data contained in all_routes.json
    :return:
    """

    timetable_paths = os.listdir(timetables_dir_path)

    station_query = "$s1 isa station, has naptan-id \"{}\";"

    def add_route_stop_relationship(naptan_id, role_played, route_id):
        match_query = station_query.format(naptan_id)
        insert_query = "$r({}: $s1) isa route, id {};".format(role_played, route_id)

        response = perform_query(match_insert(match_query, insert_query))
        check_response_length(response, min_length=1, max_length=1)

    for timetable_path in timetable_paths:
        with open(timetables_dir_path + "/" + timetable_path, 'r') as f:
            data = json.load(f)

            tube_line_query = "$tl isa tube-line, has name \"{}\";".format(data['lineName'])
            response = perform_query(match_get(tube_line_query))

            if len(response) < 1:
                # In this case we haven't already added this tube-line before
                # We do it this way so that we have explicitly asked the database if the tube-line exists, and if not
                # then we use the same query body but as an insert

                response = perform_query(insert(tube_line_query))
                check_response_length(response, min_length=1, max_length=1) # Exactly one concept should be inserted

            for station in data["stops"]:

                response = perform_query(match_get(station_query.format(station["id"])))

                if len(response) < 1:
                    # Only proceed if there is this station isn't already in the database

                    # Check that the station isn't duplicated, or skip this if instead we only do it once per tube-line.
                    # - Actually there will be duplication doing it per tube, since stations belong to more than one
                    # tube-line

                    # station_insert_query = (station_query.format(station["id"]) +
                    #                         "$s1 has name \"{}\", "
                    #                         "has lat {}, has lon {};\n"
                    #                         "$z(contained-station: $s1)").format(station["id"],
                    #                                                              station["name"],
                    #                                                              station["lat"],
                    #                                                              station["lon"],
                    #                                                              )
                    station_insert_query = (station_query.format(station["id"]) +
                                            "$s1 has name \"{}\", "
                                            "has lat {}, has lon {};\n").format(station["name"],
                                                                                station["lat"],
                                                                                station["lon"],
                                                                                )
                    response = perform_query(insert(station_insert_query))
                    check_response_length(response, min_length=1, max_length=1)

                    try:
                        zone_name = station["zone"]
                    except KeyError:
                        # In the case that there is no zone information
                        zone_name = -1

                    response = perform_query(match_get("$z isa zone, has name \"{}\";\n".format(zone_name)))

                    if len(response) < 1:
                        # If the zone doesn't already exist then insert it
                        zone_query = "$z(contained-station: $s1) isa zone, has name \"{}\";\n".format(zone_name)
                        response = perform_query(match_insert(station_query.format(station["id"]), zone_query))
                        check_response_length(response, min_length=1, max_length=1)  # Exactly one concept should be
                        # inserted

            """
            Get the time between stops
            """
            for routes in data['timetable']["routes"]:

                for station_intervals in routes["stationIntervals"]:
                    # This actually iterates over the routes, in TFL's infinite wisdom

                    last_naptan_id = data['timetable']["departureStopId"]
                    last_time_to_arrival = 0
                    route_query = "$r(route-operator: $tl) isa route;"

                    response = perform_query(match_insert(tube_line_query, route_query))
                    check_response_length(response, min_length=1, max_length=1)
                    route_id = get_match_id(response, "r")
                    # TODO Here we need to execute the query in order to get the ID of the route inserted, since that's
                    # the only way to uniquely identify it for the insertion of the route-sections below

                    add_route_stop_relationship(last_naptan_id, "origin", route_id)

                    for i, interval in enumerate(station_intervals['intervals']):

                        # === TUNNELS ===
                        # Now we need to insert a tunnel that can make the connection if there isn't one, or if there
                        # is one then don't add one and instead use its ID
                        station_pair_query = ("$s1 isa station, has naptan-id \"{}\";\n"
                                              "$s2 isa station, has naptan-id \"{}\";\n"
                                              ).format(last_naptan_id,
                                                       interval["stopId"])

                        tunnel_query = "$t(beginning: $s1, end: $s2) isa tunnel;"

                        response = perform_query(match_get(station_pair_query + tunnel_query))

                        if len(response) < 1:
                            response = perform_query(match_insert(station_pair_query, tunnel_query))

                        # Get the ID of either the pre-existing tunnel or the one just inserted
                        tunnel_id = get_match_id(response, "t")

                        # === Connect Stations to Routes ===
                        if i == len(station_intervals['intervals']) - 1:
                            # In this case we're at the last route-section of the route, ending at the last station
                            role_played = "destination"
                        else:
                            role_played = "stop"

                        add_route_stop_relationship(interval["stopId"], role_played, route_id)

                        # === Link routes to tunnels with route-sections ===
                        duration = int(interval["timeToArrival"] - last_time_to_arrival)
                        match_query = "$t id {};\n$r id {};".format(tunnel_id, route_id)
                        # insert_query = "$rs(section: $t, service: $r) isa route-section, has duration {};".format(duration)
                        insert_query = ("$rs isa route-section, has duration {}; "
                                        "$r(section: $rs);"
                                        "$t(service: $rs);").format(duration)

                        response = perform_query(match_insert(match_query, insert_query))
                        check_response_length(response, min_length=1, max_length=1)

                        # Update variables for the next iteration
                        last_time_to_arrival = interval["timeToArrival"]
                        last_naptan_id = interval["stopId"]


def make_queries(timetables_dir_path, keyspace, uri=settings.uri,
                 log_file="logs/graql_output_{}.txt".format(dt.datetime.now()), send_queries=True):
    client = grakn.Client(uri=uri, keyspace=keyspace)

    start_time = dt.datetime.now()

    with open(log_file, "w") as graql_output:

        def query_function(graql_string):
            print(graql_string)
            print("---")
            graql_output.write(graql_string)
            if send_queries:
                # Send the graql query to the server
                response = client.execute(graql_string)
                graql_output.write("\n--response:\n" + str(response))
                graql_output.write("\n{} insertions made \n ----- \n".format(len(response)))
                return response
            else:
                return [{"id": "<DUMMY_ID>"},]

        import_query_generator(query_function, timetables_dir_path)


        end_time = dt.datetime.now()
        time_taken = end_time - start_time
        time_taken_string = "----------\nTime taken: {}".format(time_taken)
        graql_output.write(time_taken_string)
        print(time_taken_string)


if __name__ == "__main__":

    go = True
    make_queries(settings.timetables_path, settings.keyspace, send_queries=go)
