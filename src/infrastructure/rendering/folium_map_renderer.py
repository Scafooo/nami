"""FoliumMapRenderer: draws MapPOIs as an interactive Leaflet map (real
OpenStreetMap-style tiles, clustered markers by category)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import folium
from folium.plugins import MarkerCluster

from application.services.map_renderer import MapRenderer
from domain.models.poi.map_poi import MapPOI

# Leaf-category -> broad group, for marker color/layer grouping. Every
# distinct leaf category actually present in the crawl (see
# src/pipeline/enrich/transform.py's TypeHierarchy) must appear here.
GROUPS: dict[str, tuple[str, str]] = {
    "Restaurant": ("Food & Drink", "orange"),
    "BarOrPub": ("Food & Drink", "orange"),
    "CafeOrCoffeeShop": ("Food & Drink", "orange"),
    "Winery": ("Food & Drink", "orange"),
    "FastFoodRestaurant": ("Food & Drink", "orange"),
    "Hotel": ("Lodging", "blue"),
    "Hostel": ("Lodging", "blue"),
    "BedAndBreakfast": ("Lodging", "blue"),
    "LodgingBusiness": ("Lodging", "blue"),
    "Campground": ("Lodging", "blue"),
    "Museum": ("Culture & Sights", "darkgreen"),
    "CivicStructure": ("Culture & Sights", "darkgreen"),
    "TouristAttraction": ("Culture & Sights", "darkgreen"),
    "ArtGallery": ("Culture & Sights", "darkgreen"),
    "Church": ("Culture & Sights", "darkgreen"),
    "PerformingArtsTheater": ("Culture & Sights", "darkgreen"),
    "MusicVenue": ("Culture & Sights", "darkgreen"),
    "TouristInformationCenter": ("Culture & Sights", "darkgreen"),
    "Landform": ("Culture & Sights", "darkgreen"),
    "NightClub": ("Nightlife", "darkpurple"),
    "Casino": ("Nightlife", "darkpurple"),
    "EntertainmentBusiness": ("Nightlife", "darkpurple"),
    "EventVenue": ("Nightlife", "darkpurple"),
    "Store": ("Shopping", "cadetblue"),
    "ShoppingCenter": ("Shopping", "cadetblue"),
    "DepartmentStore": ("Shopping", "cadetblue"),
    "ClothingStore": ("Shopping", "cadetblue"),
    "FurnitureStore": ("Shopping", "cadetblue"),
    "SportsActivityLocation": ("Sports & Wellness", "green"),
    "PublicSwimmingPool": ("Sports & Wellness", "green"),
    "ExerciseGym": ("Sports & Wellness", "green"),
    "DaySpa": ("Sports & Wellness", "green"),
    "Park": ("Nature & Parks", "lightgreen"),
    "Zoo": ("Nature & Parks", "lightgreen"),
    "LocalBusiness": ("Other", "gray"),
    "Place": ("Other", "gray"),
}


def _force_fullscreen(html_path: Path, map_div_id: str) -> None:
    """folium's own width:100%/height:100% on the map div depends on every
    ancestor having an explicit height, which the generated page doesn't
    reliably provide. Pin it to the viewport instead."""
    css = (
        f"<style>#{map_div_id}{{position:fixed !important;inset:0 !important;"
        f"width:100vw !important;height:100vh !important;}}</style>"
    )
    text = html_path.read_text()
    html_path.write_text(text.replace("</head>", css + "</head>", 1))


@dataclass
class FoliumMapRenderer(MapRenderer):
    def render(self, pois: list[MapPOI], out_file: Path) -> None:
        center_lat = sum(p.lat for p in pois) / len(pois)
        center_lon = sum(p.lon for p in pois) / len(pois)
        # Both folium's "CartoDB positron" shortcut and CARTO's raw basemap
        # CDN now require an API key (verified: tiles come back watermarked
        # "API KEY REQUIRED" without one). Esri's public World Light Gray
        # Canvas service is free, keyless, and exists for exactly this
        # purpose. Note the tile path order is {z}/{y}/{x}, not {z}/{x}/{y}.
        m = folium.Map(location=[center_lat, center_lon], zoom_start=14, max_zoom=20, tiles=None)
        folium.TileLayer(
            tiles="https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}",
            attr="Esri, HERE, Garmin, FAO, NOAA, USGS",
            name="Light Gray Canvas",
            max_zoom=20,
            max_native_zoom=16,
        ).add_to(m)

        layers: dict[str, folium.FeatureGroup] = {}
        clusters: dict[str, MarkerCluster] = {}

        for p in pois:
            group_label, color = GROUPS.get(p.category, ("Other", "gray"))
            if group_label not in layers:
                layers[group_label] = folium.FeatureGroup(name=group_label)
                clusters[group_label] = MarkerCluster()
                layers[group_label].add_child(clusters[group_label])
            short_id = p.uri.rsplit("/", 1)[-1]
            popup = folium.Popup(
                f"<b>{p.name or '(untitled)'}</b><br>{group_label} &middot; {p.category}<br>"
                f"<span style='font-family:monospace;font-size:11px'>poi/{short_id}</span>",
                max_width=260,
            )
            folium.CircleMarker(
                location=[p.lat, p.lon],
                radius=5,
                color=color,
                fill=True,
                fill_color=color,
                fill_opacity=0.85,
                weight=1,
                popup=popup,
                tooltip=p.name or p.category,
            ).add_to(clusters[group_label])

        for layer in layers.values():
            layer.add_to(m)
        folium.LayerControl(collapsed=False).add_to(m)

        out_file.parent.mkdir(parents=True, exist_ok=True)
        m.save(str(out_file))
        _force_fullscreen(out_file, m.get_name())
