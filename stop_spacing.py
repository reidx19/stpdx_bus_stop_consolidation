#%%
import geopandas as gpd
import folium
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
# rail_routes = gpd.read_file("TriMet_GIS/GIS_Data.gdb",layer="trimet_rail_routes_202605")

# consolidate routes
# routes = routes.groupby(["rte","dir"])
routes.drop(columns=["Shape_Length"],inplace=True)
routes.sort_values(["rte","dir","frequent"],ascending=[True,True,False],inplace=True)
routes = routes[routes[["rte","dir"]].duplicated()==False]

#%%
buffer_ft = 1/8 * 5280 / 2
stops.geometry = stops.buffer(buffer_ft)

#%% for each stop, check if it's close to another stop
# note, only count these if one stop isn't servicing other stops

stops_with_multiple_routes = stops.groupby("stop_id").agg(
    routes=("rte","unique"),
    num_routes=("rte","nunique")
    )
stops_with_multiple_routes = stops_with_multiple_routes[stops_with_multiple_routes["num_routes"]>1]

transfer_points = gpd.overlay(
    stops_point[["rte","stop_id","geometry"]].drop_duplicates(),
    stops[["rte","geometry"]]
    )
transfer_points = transfer_points.dissolve(
    by="stop_id",
    aggfunc={
        "rte_2" : [("routes","unique"), ("num_routes","nunique")]
    }
)
transfer_points.columns = [x[1] if isinstance(x,tuple) else x for x in transfer_points.columns]
transfer_points.reset_index(inplace=True)

transfer_points = transfer_points.merge(stops_with_multiple_routes,on="stop_id",suffixes=(None,"_stop"))

#%% transfer points have to service routes that aren't already attached to the route
cond1 = transfer_points.apply(
    lambda row: len(
        set(row["routes"].tolist()) - set(row["routes_stop"].tolist())
        ) > 0,
    axis=1
)
cond2 = transfer_points["num_routes"]>1
transfer_points = transfer_points[cond1 & cond2]

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

# add to the routes
routes = routes.merge(route_statistics[["rte","dir","total_num_stops","mean_num_stops","max_num_stops","min_num_stops"]],on=["rte","dir"])

#%%

# stops_point = stops_point[stops["type"]=="BUS"]

# %% exports
route_statistics.to_csv(here()/"data/route_statistics.csv",index=False)
transfer_points.to_crs("epsg:4326").to_file(here()/"data/transfer_points.geojson")
too_close.reset_index().to_crs("epsg:4326").to_file(here()/"data/stops_within_1_16_mile.geojson")
stops_point.to_crs("epsg:4326").to_file(here()/"data/stops.geojson")

routes.to_crs("epsg:4326").to_file(here()/"data/routes.geojson")

#%%
center = stops_point.to_crs("epsg:4326").union_all().centroid
(center.x,center.y)

#%%