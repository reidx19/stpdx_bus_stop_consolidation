#%%
import geopandas as gpd
import pandas as pd
from pathlib import Path
import math
from pyprojroot import here

from itertools import chain

#%%
gpd.list_layers(here()/"TriMet_GIS/GIS_Data.gdb")

#%%###########################################################################
# Load stops and routes
##############################################################################
stops = gpd.read_file(here()/"TriMet_GIS/GIS_Data.gdb",layer="trimet_stops_202605")
routes = gpd.read_file("TriMet_GIS/GIS_Data.gdb",layer="trimet_routes_202605")

##############################################################################
# Add Supplemental Data
##############################################################################

# ----------------------------------------------------------------------------
# stop inventory
stop_inventory = pd.read_csv(here()/"data/Stop Inventory.csv")

not_all_null = stop_inventory.loc[:,'Landing Size':'Shelter Rating'].isna().all(axis=1) == False
stop_inventory = stop_inventory[not_all_null]

#simplify
sidewalk_exists = ["YES","PARTIAL","Yes, to intersection with curb ramp"]
no_sidewalk = ["NO", "No sidewalk", 'Partial, not accessible'] 
stop_inventory["sidewalk_exists"] = stop_inventory["Sidewalk Exists"].case_when(
    caselist=[
        (stop_inventory["Sidewalk Exists"].isin(sidewalk_exists), "yes"),
        (stop_inventory["Sidewalk Exists"].isin(no_sidewalk), "no"),
        (stop_inventory["Sidewalk Exists"].isna() == False, "unknown")
    ]
)

accessible_path = ["EXISTS","Existing path"]
no_accessible_path = ["NONE","N/A (no sidewalk/pad/not needed)", "No path"]
stop_inventory["accessible_path"] = stop_inventory["Accessible Path"].case_when(
    caselist=[
        (stop_inventory["Accessible Path"].isin(accessible_path), "yes"),
        (stop_inventory["Accessible Path"].isin(no_accessible_path), "no"),
        (stop_inventory["Accessible Path"].isna() == False, "unknown")
    ]
)

stops = stops.merge(
    stop_inventory[["stop_id","sidewalk_exists","accessible_path"]],
    on="stop_id",
    how='left'
    )

# ----------------------------------------------------------------------------
# stop boardings/alightings

stop_stats = pd.read_csv(here()/"data/trimet_passenger_census_weekday.csv")
stops = stops.merge(
    stop_stats[["Location ID","Ons","Offs","Total","Monthly Lifts"]],
    left_on = "stop_id",
    right_on = "Location ID",
    how = 'left'
)

# stop_supplemental_info = stops[["stop_id","sidewalk_exists","accessible_path","Ons","Offs","Total","Monthly Lifts"]]

##############################################################################
# GIS Analysis
##############################################################################

# ----------------------------------------------------------------------------
# data wrangling

# stops = stops[stops["type"]=="BUS"]
stops_point = stops.copy()

# consolidate routes
# routes = routes.groupby(["rte","dir"])
routes.drop(columns=["Shape_Length"],inplace=True)
routes.sort_values(["rte","dir","frequent"],ascending=[True,True,False],inplace=True)
routes = routes[routes[["rte","dir"]].duplicated()==False]

#%% ----------------------------------------------------------------------------
# set buffer and tolerances
# this is used to identify stops that are too close together
tolerance_ft = 5
stop_spacing_mi = 1/8
buffer_ft = (stop_spacing_mi * 5280 / 2) # 1/8 mi. stop spacing means 1/16 mi. buffers
stops.geometry = stops.buffer(buffer_ft+tolerance_ft)

transfer_distance_ft = 300 / 2
transfer_buffer = stops_point.copy()
transfer_buffer.geometry = transfer_buffer.buffer(transfer_distance_ft)

#%% ----------------------------------------------------------------------------
# identify routes serviced by each stop

routes_serviced_by_stop = transfer_buffer.groupby("stop_id").agg(
    routes=("rte","unique"),
    num_routes=("rte","nunique")
    )
routes_serviced_by_stop = pd.merge(
    transfer_buffer[["stop_id","geometry"]].drop_duplicates(),
    routes_serviced_by_stop,
    on="stop_id"
    )

#%% ----------------------------------------------------------------------------
# identify transfer points
# these are stops that are near other stops that serve different routes
# direction shouldn't matter here

transfer_points = gpd.overlay(
    routes_serviced_by_stop,
    routes_serviced_by_stop
)
transfer_points = transfer_points[transfer_points["stop_id_1"]!=transfer_points["stop_id_2"]]

def compare_set(routes1,routes2):
    # if all routes in route 1 are in route 2, not a transfer
    if (set(routes1) - set(routes2) == set()):
        return False
    # if same set, then it's a dup
    elif (set(routes1)==set(routes2)):
        return False
    else:
        return True

transfer_points = (
    transfer_points[transfer_points.apply(lambda row: compare_set(row["routes_1"],row["routes_2"]), axis=1)]
    .groupby("stop_id_1").agg(
        stops_to_transfer_to = ("stop_id_2", lambda x: [int(x) for x in list(set(x))]),
        routes_to_transfer_to = ("routes_2", lambda x: [int(x) for x in sorted(list(set(chain.from_iterable(x))))])
    )
    .reset_index()
    .rename(columns={"stop_id_1":"stop_id"})
)
transfer_points["num_stops_to_transfer_to"] = transfer_points["stops_to_transfer_to"].apply(lambda x: len(x))
transfer_points["num_routes_to_transfer_to"] = transfer_points["routes_to_transfer_to"].apply(lambda x: len(x))
transfer_points = stops_point.merge(transfer_points)

transfer_points.to_file(Path.home()/"Downloads/test.geojson")
transfer_buffer.to_file(Path.home()/"Downloads/test2.geojson")

#%% ----------------------------------------------------------------------------
# get stop spacing
# dissolve by rte and rte_dir then explode and intersect to
# get stops that are too close together
# only keep stops with area greater than the original buffer

# transfer points are not considered in the stop spacing
# so remove these before dissolving the stops

exploded_stops = (
    stops[stops['stop_id'].isin(transfer_points['stop_id'])==False]
    .dissolve(["rte","dir"])[["geometry"]]
    .explode().reset_index().reset_index()
)

exploded_stops_w_data = gpd.overlay(
    stops_point[["stop_id","rte","dir","geometry"]],
    exploded_stops,
    how="intersection"
)
# remove stops that don't match the route id and direction
exploded_stops_w_data = exploded_stops_w_data[
    (exploded_stops_w_data["rte_1"]==exploded_stops_w_data["rte_2"]) &
    (exploded_stops_w_data["dir_1"]==exploded_stops_w_data["dir_2"])
    ]

# group it then merge back to exploded stops
# this gives us the stop ids in the buffer
exploded_stops = (
    exploded_stops_w_data
    .groupby(["index"])
    .agg(
        stop_ids = ("stop_id","unique"),
        num_stops = ("stop_id","nunique")
    )
    .merge(exploded_stops,on="index")
    .set_geometry('geometry')
)

# get area calc
exploded_stops["area_ft"] = exploded_stops.area

# stops that are too close will have more than one stop
route_cols = ["rte","dir","rte_desc","public_rte","frequent","type"]
too_close = (
    exploded_stops[exploded_stops["num_stops"]>1]
    # add back in some route information
    .merge(routes[route_cols],on=["rte","dir"])
)

# these are the number of stops we could remove
too_close["stops_to_remove"] = (too_close["num_stops"] / 2).apply(math.floor)
time_save_sec = 45
too_close["time_savings_sec"] = too_close["stops_to_remove"] * time_save_sec

#%% save how much time when removing bus stop

# get route summary statistics
route_statistics = (
    too_close
    .groupby(route_cols)
    .agg(
        remove_stops = ("stops_to_remove","sum"),
        time_savings_sec = ("time_savings_sec","sum"),
        total_num_stops = ("num_stops","sum"),
        mean_num_stops = ("num_stops","mean"),
        max_num_stops = ("num_stops","max"),
        min_num_stops = ("num_stops","min")
    )
    .reset_index()
    .sort_values("total_num_stops",ascending=False)
)

# convert to minutes
route_statistics["time_savings_min"] = (route_statistics["time_savings_sec"] / 60).round(1)

# add to the routes
routes = routes.merge(route_statistics[["rte","dir","remove_stops","time_savings_min","total_num_stops","mean_num_stops","max_num_stops","min_num_stops"]],on=["rte","dir"])

# %% exports
route_statistics.to_csv(here()/"data/route_statistics.csv",index=False)
transfer_points.to_crs("epsg:4326").to_file(here()/"data/transfer_points.geojson")
too_close.reset_index().to_crs("epsg:4326").to_file(here()/"data/stops_within_1_16_mile.geojson")
stops_point.to_crs("epsg:4326").to_file(here()/"data/stops.geojson")
stops.to_crs("epsg:4326").to_file(here()/"data/stops_buffered.geojson")
routes.to_crs("epsg:4326").to_file(here()/"data/routes.geojson")

#%%
center = stops_point.to_crs("epsg:4326").union_all().centroid
(center.x,center.y)

#%%