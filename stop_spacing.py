#%%
import geopandas as gpd
from pathlib import Path
import math
from pyprojroot import here

#%%
gpd.list_layers(here()/"TriMet_GIS/GIS_Data.gdb")

#%%
stops = gpd.read_file(here()/"TriMet_GIS/GIS_Data.gdb",layer="trimet_stops_202605")
# stops = stops[stops["type"]=="BUS"]
stops_point = stops.copy()

routes = gpd.read_file("TriMet_GIS/GIS_Data.gdb",layer="trimet_routes_202605")

#%%
buffer_ft = 1/8 * 5280 / 2
stops.geometry = stops.buffer(buffer_ft)

#%% for each stop, check if it's close to another stop
transfer_points = gpd.overlay(stops_point[["rte","stop_id","geometry"]].drop_duplicates(),stops[["rte","geometry"]])
transfer_points = transfer_points.dissolve(
    by="stop_id",
    aggfunc={
        "rte_2" : [("routes","unique"), ("num_routes","nunique")]
    }
)
transfer_points.columns = [x[1] if isinstance(x,tuple) else x for x in transfer_points.columns]
transfer_points.reset_index(inplace=True)

# transfer points have to have more than one route
transfer_points = transfer_points[transfer_points["num_routes"]>1]

#%% dissolve by route id and direction, then explode and
# only keep stops with area greater than the original buffer
# also remove transfer points from the analysis
dissolved_stops = stops[stops["stop_id"].isin(transfer_points["stop_id"])==False].dissolve(["rte","dir"])[["geometry"]]

# explode geo to get single parts and reset the index
exploded_stops = dissolved_stops.explode().reset_index().reset_index()
    
# intersect with the stops to add that info in
exploded_stops_w_data = gpd.overlay(stops_point[["stop_id","geometry"]],exploded_stops,how="intersection")

# group it then merge back to exploded stops
exploded_stops_w_data = exploded_stops_w_data.groupby(["index"]).agg(
    stop_ids = ("stop_id","unique"),
    num_stops = ("stop_id","nunique")
)
exploded_stops = exploded_stops.merge(exploded_stops_w_data,on="index")

# get area calc
exploded_stops["area_ft"] = exploded_stops.area

# find stops that are too close
too_close = exploded_stops[exploded_stops["area_ft"] > buffer_ft**2 * math.pi]

# add back in some route information
route_cols = ["rte","dir","rte_desc","public_rte","frequent","type"]
too_close = too_close.merge(routes[route_cols],on=["rte","dir"])


#%% save how much time when removing bus stop

time_save_sec = 45

# get route summary statistics
route_statistics = too_close.groupby(route_cols).agg(
    total_num_stops = ("num_stops","sum"),
    mean_num_stops = ("num_stops","mean"),
    max_num_stops = ("num_stops","max"),
    min_num_stops = ("num_stops","min")
).reset_index().sort_values("total_num_stops",ascending=False)

# %% exports
route_statistics.to_csv(here()/"data/route_statistics.csv",index=False)
transfer_points.to_crs("epsg:4326").to_file(here()/"data/transfer_points.geojson")
too_close.reset_index().to_crs("epsg:4326").to_file(here()/"data/stops_within_1_16_mile.geojson")
stops_point.to_crs("epsg:4326").to_file(here()/"data/stops.geojson")

routes.to_crs("epsg:4326").to_file(here()/"data/routes.geojson")

#%%