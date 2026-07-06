"""
Core logic for RaG Economy Manager.

This module deliberately knows nothing about Tk. Keep it that way.
"""

from __future__ import annotations

import copy
import csv
import io
import json
import math
import os
import re
import shutil
import struct
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Iterable
from xml.sax.saxutils import escape

try:
    from pbo_core import PboError, read_pbo_archive, read_pbo_entry_data
except Exception:  # pragma: no cover - packaged builds should include pbo_core.py
    PboError = Exception
    read_pbo_archive = None
    read_pbo_entry_data = None


NUMERIC_FIELDS = ("nominal", "lifetime", "restock", "min", "quantmin", "quantmax", "cost")
EVENT_REPORT_FIELDS = ("nominal", "min", "max", "lifetime", "restock", "saferadius", "distanceradius", "cleanupradius", "secondary", "deletable", "init_random", "remove_damaged", "position", "limit", "active", "children")
EVENT_CHILD_ORDER = ("nominal", "min", "max", "lifetime", "restock", "saferadius", "distanceradius", "cleanupradius", "secondary", "flags", "position", "limit", "active", "children")
EVENT_NUMERIC_FIELDS = ("nominal", "min", "max", "lifetime", "restock", "saferadius", "distanceradius", "cleanupradius")
EVENT_FLAG_FIELDS = ("deletable", "init_random", "remove_damaged")
EVENT_POSITION_VALUES = ("fixed", "player", "uniform")
EVENT_LIMIT_VALUES = ("child", "parent", "mixed", "custom")
TERRITORY_REPORT_FIELDS = ("name", "x", "z", "r", "dmin", "dmax")
BULK_FIELDS = ("nominal", "min", "lifetime", "restock")
CONFIG_TYPE_SECTIONS = ("CfgVehicles", "CfgWeapons", "CfgMagazines")
CONFIG_GENERATED_CATEGORIES = ("tools", "containers", "clothes", "lootdispatch", "food", "weapons", "books", "explosives")
CONFIG_FILE_SUFFIXES = (".json", ".cfg", ".c")
RELATION_FIELDS = ("category", "usage", "value", "tag")
TYPE_CHILD_ORDER = ("nominal", "lifetime", "restock", "min", "quantmin", "quantmax", "cost", "flags", "category", "tag", "usage", "value")
TYPE_RELATION_ORDER = ("category", "tag", "usage", "value")
RELATION_DEFINITION_GROUPS = {
    "category": ("categories", "category"),
    "usage": ("usageflags", "usage"),
    "value": ("valueflags", "value"),
    "tag": ("tags", "tag"),
}
FLAG_FIELDS = ("count_in_cargo", "count_in_hoarder", "count_in_map", "count_in_player", "crafted", "deloot")
MISSION_TYPES_FILENAME = "types.xml"
MISSION_SPAWNABLE_TYPES_FILENAME = "cfgspawnabletypes.xml"
MISSION_RANDOM_PRESETS_FILENAME = "cfgrandompresets.xml"
MISSION_EVENTS_FILENAME = "events.xml"
MISSION_EVENT_SPAWNS_FILENAME = "cfgeventspawns.xml"
MISSION_EVENT_GROUPS_FILENAME = "cfgeventgroups.xml"
MISSION_LIMITS_FILENAME = "cfglimitsdefinition.xml"
MISSION_ECONOMYCORE_FILENAME = "cfgeconomycore.xml"
MISSION_ENVIRONMENT_FILENAME = "cfgenvironment.xml"
MISSION_WEATHER_FILENAME = "cfgweather.xml"
MISSION_MAPGROUPPROTO_FILENAME = "mapgroupproto.xml"
MISSION_MAPGROUPPOS_FILENAME = "mapgrouppos.xml"
MISSION_DB_DIRNAME = "db"
MISSION_XML_SCAN_DIRS = (MISSION_DB_DIRNAME, "env", "events", "cfgeconomycore")
IGNORED_STORAGE_DIRNAME = "storage_1"
TERRITORY_FILENAME_SUFFIX = "_territories.xml"
EVENT_TERRITORY_FILE_HINTS = {
    "animalbear": ("bear",),
    "animalcow": ("cattle", "cow"),
    "animaldeer": ("red_deer", "deer"),
    "animalgoat": ("sheep_goat", "goat"),
    "animalpig": ("pig",),
    "animalroedeer": ("roe_deer", "roedeer"),
    "animalsheep": ("sheep_goat", "sheep"),
    "animalwildboar": ("wild_boar", "wildboar"),
    "animalwolf": ("wolf",),
    "ambientfox": ("fox",),
    "ambienthare": ("hare",),
    "ambienthen": ("hen",),
}
EVENT_PREFIX_FAMILIES = ("Ambient", "Animal", "Infected", "Item", "Static", "Trajectory", "Vehicle")
SPECIAL_EVENT_NAMES = ("Loot",)
EVENT_FAMILY_RULES = (
    ("Ambient", "Ambient territory event", "environment"),
    ("Animal", "Animal territory event", "environment"),
    ("Infected", "Infected territory event", "environment"),
    ("Trajectory", "Trajectory map group event", "mapgroup"),
    ("Vehicle", "Vehicle fixed-position event", "cfgeventspawns"),
    ("Static", "Static fixed-position event", "cfgeventspawns"),
    ("Item", "Item fixed-position event", "cfgeventspawns"),
)
ENVIRONMENT_ELEMENT_EXPLANATIONS = {
    "env": "Root of cfgenvironment.xml.",
    "territories": "Registers territory XML files and maps named environment populations to those files.",
    "file": "At territories level, path loads an env territory XML file. Inside a territory, usable links that population to a registered file stem.",
    "territory": "Defines one environment population, its behavior, linked territory files, agents, and runtime limits.",
    "agent": "Weighted agent group used by an Ambient or custom environment population.",
    "spawn": "Concrete animal, infected, or custom agent class that this agent group may spawn.",
    "item": "Runtime setting for a territory or agent. Name selects the setting; val supplies its value.",
}
ENVIRONMENT_ATTRIBUTE_EXPLANATIONS = {
    ("file", "path"): "Mission-relative path to an env/*_territories.xml file. This registers the file for use below.",
    ("file", "usable"): "Registered territory file stem without path or .xml, for example red_deer_territories.",
    ("territory", "type"): "Population controller type. Common values are Herd and Ambient.",
    ("territory", "name"): "Environment population name used to connect Animal, Ambient, or Infected events to territory zones.",
    ("territory", "behavior"): "DayZ AI group behavior class controlling this population.",
    ("agent", "type"): "Logical agent variant, commonly Male or Female. Custom definitions may use other names.",
    ("agent", "chance"): "Relative weight for choosing this agent variant.",
    ("spawn", "configName"): "DayZ or modded agent class name to spawn. Agent classes generally begin with Animal_ or another registered AgentType class.",
    ("spawn", "chance"): "Relative weight for choosing this concrete spawn class.",
    ("item", "name"): "Environment runtime setting name, such as globalCountMax, zoneCountMin, countMin, or playerSpawnRadiusNear.",
    ("item", "val"): "Value assigned to the named environment runtime setting.",
}
ENVIRONMENT_ITEM_EXPLANATIONS = {
    "globalcountmax": "Maximum total agents from this population across the map.",
    "zonecountmin": "Minimum agents created when a territory zone activates.",
    "zonecountmax": "Maximum agents created when a territory zone activates.",
    "playerspawnradiusnear": "Closest allowed spawn distance from a player, in meters.",
    "playerspawnradiusfar": "Farthest spawn search distance from a player, in meters.",
    "zonetouchdisableeditperiodsec": "Seconds a touched zone remains protected from population edits.",
    "herdscount": "Number of herds of this population maintained on the map.",
    "countmin": "Minimum count for this agent group.",
    "countmax": "Maximum count for this agent group.",
}


def is_ignored_storage_path(path: str | os.PathLike[str]) -> bool:
    try:
        absolute = os.path.abspath(os.fspath(path))
    except (TypeError, ValueError, OSError):
        return False
    if any(part.casefold() == IGNORED_STORAGE_DIRNAME for part in Path(absolute).parts):
        return True
    try:
        resolved = os.path.realpath(absolute)
    except OSError:
        return False
    return any(part.casefold() == IGNORED_STORAGE_DIRNAME for part in Path(resolved).parts)


def ensure_not_ignored_storage_path(path: str | os.PathLike[str]) -> None:
    if is_ignored_storage_path(path):
        raise OSError(f"Access blocked: {IGNORED_STORAGE_DIRNAME} is always ignored.")


def iter_files_ignoring_storage(root: str | os.PathLike[str]):
    if is_ignored_storage_path(root):
        return
    for current_root, dirnames, filenames in os.walk(root, topdown=True):
        dirnames[:] = [name for name in dirnames if name.casefold() != IGNORED_STORAGE_DIRNAME]
        for filename in filenames:
            yield Path(current_root) / filename


def parse_xml_file(path: str | os.PathLike[str], parser: ET.XMLParser | None = None) -> ET.ElementTree:
    ensure_not_ignored_storage_path(path)
    return ET.parse(path, parser=parser)

WEATHER_FIELD_DEFINITIONS = (
    ("reset", "Reset stored weather", "0/1. 1 ignores stored weather state and starts from this file."),
    ("enable", "Enable cfgweather.xml", "0/1. 1 lets cfgweather.xml control weather; 0 lets default/storage behavior win."),
    ("overcast.current.actual", "Initial overcast", "0 clear sky, 1 fully overcast."),
    ("overcast.current.time", "Overcast start time", "Seconds to reach initial overcast target."),
    ("overcast.current.duration", "Overcast hold duration", "Seconds to hold initial overcast target."),
    ("overcast.limits.min", "Overcast min", "Lowest random overcast value."),
    ("overcast.limits.max", "Overcast max", "Highest random overcast value."),
    ("overcast.timelimits.min", "Overcast min change time", "Fastest random overcast transition, seconds."),
    ("overcast.timelimits.max", "Overcast max change time", "Slowest random overcast transition, seconds."),
    ("overcast.changelimits.min", "Overcast min change", "Smallest random overcast delta."),
    ("overcast.changelimits.max", "Overcast max change", "Largest random overcast delta."),
    ("fog.current.actual", "Initial fog", "0 no fog, 1 dense fog."),
    ("fog.current.time", "Fog start time", "Seconds to reach initial fog target."),
    ("fog.current.duration", "Fog hold duration", "Seconds to hold initial fog target."),
    ("fog.limits.min", "Fog min", "Lowest random fog value."),
    ("fog.limits.max", "Fog max", "Highest random fog value."),
    ("fog.timelimits.min", "Fog min change time", "Fastest random fog transition, seconds."),
    ("fog.timelimits.max", "Fog max change time", "Slowest random fog transition, seconds."),
    ("fog.changelimits.min", "Fog min change", "Smallest random fog delta."),
    ("fog.changelimits.max", "Fog max change", "Largest random fog delta."),
    ("rain.current.actual", "Initial rain", "0 dry, 1 heavy rain."),
    ("rain.current.time", "Rain start time", "Seconds to reach initial rain target."),
    ("rain.current.duration", "Rain hold duration", "Seconds to hold initial rain target."),
    ("rain.limits.min", "Rain min", "Lowest random rain value."),
    ("rain.limits.max", "Rain max", "Highest random rain value."),
    ("rain.timelimits.min", "Rain min change time", "Fastest random rain transition, seconds."),
    ("rain.timelimits.max", "Rain max change time", "Slowest random rain transition, seconds."),
    ("rain.changelimits.min", "Rain min change", "Smallest random rain delta."),
    ("rain.changelimits.max", "Rain max change", "Largest random rain delta."),
    ("rain.thresholds.min", "Rain overcast threshold min", "Rain can start only when overcast is at least this value."),
    ("rain.thresholds.max", "Rain overcast threshold max", "Rain allowed until this overcast value."),
    ("rain.thresholds.end", "Rain stop time", "Seconds for rain to stop outside threshold."),
    ("windMagnitude.current.actual", "Initial wind speed", "Initial wind speed target in m/s."),
    ("windMagnitude.current.time", "Wind speed start time", "Seconds to reach initial wind speed target."),
    ("windMagnitude.current.duration", "Wind speed hold duration", "Seconds to hold initial wind speed target."),
    ("windMagnitude.limits.min", "Wind speed min", "Lowest random wind speed in m/s."),
    ("windMagnitude.limits.max", "Wind speed max", "Highest random wind speed in m/s."),
    ("windMagnitude.timelimits.min", "Wind speed min change time", "Fastest random wind speed transition, seconds."),
    ("windMagnitude.timelimits.max", "Wind speed max change time", "Slowest random wind speed transition, seconds."),
    ("windMagnitude.changelimits.min", "Wind speed min change", "Smallest random wind speed delta."),
    ("windMagnitude.changelimits.max", "Wind speed max change", "Largest random wind speed delta."),
    ("windDirection.current.actual", "Initial wind direction", "Initial wind direction angle in radians."),
    ("windDirection.current.time", "Wind direction start time", "Seconds to reach initial wind direction target."),
    ("windDirection.current.duration", "Wind direction hold duration", "Seconds to hold initial wind direction target."),
    ("windDirection.limits.min", "Wind direction min", "Lowest random wind direction angle in radians."),
    ("windDirection.limits.max", "Wind direction max", "Highest random wind direction angle in radians."),
    ("windDirection.timelimits.min", "Wind direction min change time", "Fastest random wind direction transition, seconds."),
    ("windDirection.timelimits.max", "Wind direction max change time", "Slowest random wind direction transition, seconds."),
    ("windDirection.changelimits.min", "Wind direction min change", "Smallest random wind direction delta."),
    ("windDirection.changelimits.max", "Wind direction max change", "Largest random wind direction delta."),
    ("snowfall.current.actual", "Initial snowfall", "0 no snow, 1 heavy snow."),
    ("snowfall.current.time", "Snowfall start time", "Seconds to reach initial snowfall target."),
    ("snowfall.current.duration", "Snowfall hold duration", "Seconds to hold initial snowfall target."),
    ("snowfall.limits.min", "Snowfall min", "Lowest random snowfall value."),
    ("snowfall.limits.max", "Snowfall max", "Highest random snowfall value."),
    ("snowfall.timelimits.min", "Snowfall min change time", "Fastest random snowfall transition, seconds."),
    ("snowfall.timelimits.max", "Snowfall max change time", "Slowest random snowfall transition, seconds."),
    ("snowfall.changelimits.min", "Snowfall min change", "Smallest random snowfall delta."),
    ("snowfall.changelimits.max", "Snowfall max change", "Largest random snowfall delta."),
    ("snowfall.thresholds.min", "Snowfall overcast threshold min", "Snow can start only when overcast is at least this value."),
    ("snowfall.thresholds.max", "Snowfall overcast threshold max", "Snow allowed until this overcast value."),
    ("snowfall.thresholds.end", "Snowfall stop time", "Seconds for snowfall to stop outside threshold."),
    ("storm.density", "Lightning density", "0 no lightning, 1 frequent lightning."),
    ("storm.threshold", "Lightning overcast threshold", "Lightning can happen when overcast reaches this value."),
    ("storm.timeout", "Lightning timeout", "Seconds between lightning strikes."),
)

WEATHER_DEFAULT_VALUES = {
    "reset": "0",
    "enable": "0",
    "overcast.current.actual": "0.45",
    "overcast.current.time": "120",
    "overcast.current.duration": "240",
    "overcast.limits.min": "0.0",
    "overcast.limits.max": "1.0",
    "overcast.timelimits.min": "600",
    "overcast.timelimits.max": "900",
    "overcast.changelimits.min": "0.0",
    "overcast.changelimits.max": "1.0",
    "fog.current.actual": "0.05",
    "fog.current.time": "120",
    "fog.current.duration": "240",
    "fog.limits.min": "0.02",
    "fog.limits.max": "0.08",
    "fog.timelimits.min": "900",
    "fog.timelimits.max": "900",
    "fog.changelimits.min": "0.0",
    "fog.changelimits.max": "1.0",
    "rain.current.actual": "0.0",
    "rain.current.time": "60",
    "rain.current.duration": "120",
    "rain.limits.min": "0.0",
    "rain.limits.max": "1.0",
    "rain.timelimits.min": "60",
    "rain.timelimits.max": "120",
    "rain.changelimits.min": "0.0",
    "rain.changelimits.max": "1.0",
    "rain.thresholds.min": "0.6",
    "rain.thresholds.max": "1.0",
    "rain.thresholds.end": "60",
    "windMagnitude.current.actual": "8.0",
    "windMagnitude.current.time": "120",
    "windMagnitude.current.duration": "240",
    "windMagnitude.limits.min": "0.0",
    "windMagnitude.limits.max": "20.0",
    "windMagnitude.timelimits.min": "120",
    "windMagnitude.timelimits.max": "240",
    "windMagnitude.changelimits.min": "0.0",
    "windMagnitude.changelimits.max": "20.0",
    "windDirection.current.actual": "0.0",
    "windDirection.current.time": "120",
    "windDirection.current.duration": "240",
    "windDirection.limits.min": "-3.14",
    "windDirection.limits.max": "3.14",
    "windDirection.timelimits.min": "60",
    "windDirection.timelimits.max": "120",
    "windDirection.changelimits.min": "-1.0",
    "windDirection.changelimits.max": "1.0",
    "snowfall.current.actual": "0.0",
    "snowfall.current.time": "0",
    "snowfall.current.duration": "32768",
    "snowfall.limits.min": "0.0",
    "snowfall.limits.max": "0.0",
    "snowfall.timelimits.min": "300",
    "snowfall.timelimits.max": "3600",
    "snowfall.changelimits.min": "0.0",
    "snowfall.changelimits.max": "0.0",
    "snowfall.thresholds.min": "1.0",
    "snowfall.thresholds.max": "1.0",
    "snowfall.thresholds.end": "120",
    "storm.density": "1.0",
    "storm.threshold": "0.9",
    "storm.timeout": "45",
}

WEATHER_PRESETS = {
    "Spring Clear": {
        "overcast.current.actual": "0.25", "overcast.limits.min": "0.05", "overcast.limits.max": "0.55",
        "fog.current.actual": "0.05", "fog.limits.max": "0.25",
        "rain.current.actual": "0.0", "rain.limits.max": "0.25", "rain.thresholds.min": "0.55",
        "windMagnitude.current.actual": "3.0", "windMagnitude.limits.max": "10.0",
        "snowfall.current.actual": "0.0", "snowfall.limits.max": "0.0", "storm.density": "0.0",
    },
    "Spring Showers": {
        "overcast.current.actual": "0.65", "overcast.limits.min": "0.35", "overcast.limits.max": "0.95",
        "fog.current.actual": "0.1", "fog.limits.max": "0.35",
        "rain.current.actual": "0.25", "rain.limits.min": "0.0", "rain.limits.max": "0.65", "rain.thresholds.min": "0.45",
        "windMagnitude.current.actual": "5.0", "windMagnitude.limits.max": "16.0",
        "snowfall.current.actual": "0.0", "snowfall.limits.max": "0.0", "storm.density": "0.1",
    },
    "Sunny": {
        "overcast.current.actual": "0.1", "overcast.limits.min": "0.0", "overcast.limits.max": "0.35",
        "fog.current.actual": "0.0", "fog.limits.min": "0.0", "fog.limits.max": "0.12",
        "rain.current.actual": "0.0", "rain.limits.max": "0.0",
        "windMagnitude.current.actual": "2.0", "windMagnitude.limits.max": "6.0", "windMagnitude.changelimits.max": "1.5",
        "snowfall.current.actual": "0.0", "snowfall.limits.max": "0.0", "storm.density": "0.0",
    },
    "Hot Dry": {
        "overcast.current.actual": "0.05", "overcast.limits.min": "0.0", "overcast.limits.max": "0.25",
        "fog.current.actual": "0.0", "fog.limits.min": "0.0", "fog.limits.max": "0.05",
        "rain.current.actual": "0.0", "rain.limits.max": "0.0",
        "windMagnitude.current.actual": "2.0", "windMagnitude.limits.max": "8.0",
        "snowfall.current.actual": "0.0", "snowfall.limits.max": "0.0", "storm.density": "0.0",
    },
    "Cloudy": {
        "overcast.current.actual": "0.55", "overcast.limits.min": "0.35", "overcast.limits.max": "0.8",
        "fog.current.actual": "0.05", "fog.limits.max": "0.22",
        "rain.current.actual": "0.0", "rain.limits.max": "0.15",
        "windMagnitude.current.actual": "3.0", "windMagnitude.limits.max": "10.0",
        "snowfall.current.actual": "0.0", "snowfall.limits.max": "0.0", "storm.density": "0.0",
    },
    "Autumn Clear": {
        "overcast.current.actual": "0.35", "overcast.limits.min": "0.1", "overcast.limits.max": "0.65",
        "fog.current.actual": "0.12", "fog.limits.max": "0.4",
        "rain.current.actual": "0.0", "rain.limits.max": "0.2",
        "windMagnitude.current.actual": "4.0", "windMagnitude.limits.max": "14.0",
        "snowfall.current.actual": "0.0", "snowfall.limits.max": "0.0", "storm.density": "0.0",
    },
    "Rainy": {
        "overcast.current.actual": "0.85", "overcast.limits.min": "0.65", "overcast.limits.max": "1.0",
        "fog.current.actual": "0.15", "fog.limits.max": "0.35",
        "rain.current.actual": "0.55", "rain.limits.min": "0.25", "rain.limits.max": "0.85",
        "rain.thresholds.min": "0.55", "windMagnitude.current.actual": "6.0", "windMagnitude.limits.max": "18.0",
        "snowfall.current.actual": "0.0", "snowfall.limits.max": "0.0", "storm.density": "0.0",
    },
    "Autumn Rain": {
        "overcast.current.actual": "0.8", "overcast.limits.min": "0.55", "overcast.limits.max": "1.0",
        "fog.current.actual": "0.25", "fog.limits.max": "0.55",
        "rain.current.actual": "0.45", "rain.limits.min": "0.15", "rain.limits.max": "0.9", "rain.thresholds.min": "0.5",
        "windMagnitude.current.actual": "7.0", "windMagnitude.limits.max": "22.0",
        "snowfall.current.actual": "0.0", "snowfall.limits.max": "0.0", "storm.density": "0.2",
    },
    "Windy Rain": {
        "overcast.current.actual": "0.9", "overcast.limits.min": "0.6", "overcast.limits.max": "1.0",
        "fog.current.actual": "0.18", "fog.limits.max": "0.4",
        "rain.current.actual": "0.55", "rain.limits.min": "0.2", "rain.limits.max": "0.95", "rain.thresholds.min": "0.55",
        "windMagnitude.current.actual": "12.0", "windMagnitude.limits.min": "5.0", "windMagnitude.limits.max": "28.0",
        "snowfall.current.actual": "0.0", "snowfall.limits.max": "0.0", "storm.density": "0.35",
    },
    "Mixed": {
        "overcast.current.actual": "0.45", "overcast.limits.min": "0.0", "overcast.limits.max": "1.0",
        "fog.current.actual": "0.1", "fog.limits.max": "0.35",
        "rain.current.actual": "0.0", "rain.limits.min": "0.0", "rain.limits.max": "0.65",
        "windMagnitude.current.actual": "4.0", "windMagnitude.limits.max": "20.0",
        "snowfall.current.actual": "0.0", "snowfall.limits.max": "0.0", "storm.density": "0.2",
    },
    "Storm": {
        "overcast.current.actual": "0.95", "overcast.limits.min": "0.75", "overcast.limits.max": "1.0",
        "fog.current.actual": "0.2", "fog.limits.max": "0.45",
        "rain.current.actual": "0.85", "rain.limits.min": "0.45", "rain.limits.max": "1.0",
        "rain.thresholds.min": "0.6", "windMagnitude.current.actual": "10.0", "windMagnitude.limits.min": "4.0", "windMagnitude.limits.max": "26.0",
        "snowfall.current.actual": "0.0", "snowfall.limits.max": "0.0",
        "storm.density": "0.8", "storm.threshold": "0.7", "storm.timeout": "20",
    },
    "Thunder": {
        "overcast.current.actual": "0.9", "overcast.limits.min": "0.7", "overcast.limits.max": "1.0",
        "rain.current.actual": "0.35", "rain.limits.max": "0.7",
        "windMagnitude.current.actual": "8.0", "windMagnitude.limits.max": "22.0",
        "snowfall.current.actual": "0.0", "snowfall.limits.max": "0.0",
        "storm.density": "1.0", "storm.threshold": "0.65", "storm.timeout": "12",
    },
    "Foggy": {
        "overcast.current.actual": "0.35", "overcast.limits.min": "0.2", "overcast.limits.max": "0.65",
        "fog.current.actual": "0.45", "fog.limits.min": "0.25", "fog.limits.max": "0.75",
        "rain.current.actual": "0.0", "rain.limits.max": "0.15",
        "windMagnitude.current.actual": "1.0", "windMagnitude.limits.max": "6.0",
        "snowfall.current.actual": "0.0", "snowfall.limits.max": "0.0", "storm.density": "0.0",
    },
    "Winter Clear": {
        "overcast.current.actual": "0.3", "overcast.limits.min": "0.1", "overcast.limits.max": "0.65",
        "fog.current.actual": "0.08", "fog.limits.max": "0.25",
        "rain.current.actual": "0.0", "rain.limits.max": "0.0",
        "windMagnitude.current.actual": "3.0", "windMagnitude.limits.max": "10.0",
        "snowfall.current.actual": "0.0", "snowfall.limits.max": "0.2", "snowfall.thresholds.min": "0.55",
        "storm.density": "0.0",
    },
    "Winter Fog": {
        "overcast.current.actual": "0.55", "overcast.limits.min": "0.25", "overcast.limits.max": "0.85",
        "fog.current.actual": "0.55", "fog.limits.min": "0.3", "fog.limits.max": "0.85",
        "rain.current.actual": "0.0", "rain.limits.max": "0.0",
        "windMagnitude.current.actual": "1.0", "windMagnitude.limits.max": "7.0",
        "snowfall.current.actual": "0.1", "snowfall.limits.max": "0.45", "snowfall.thresholds.min": "0.45",
        "storm.density": "0.0",
    },
    "Snowy": {
        "overcast.current.actual": "0.65", "overcast.limits.min": "0.35", "overcast.limits.max": "0.9",
        "fog.current.actual": "0.1", "fog.limits.max": "0.25",
        "rain.current.actual": "0.0", "rain.limits.max": "0.0",
        "windMagnitude.current.actual": "4.0", "windMagnitude.limits.max": "12.0",
        "snowfall.current.actual": "0.35", "snowfall.limits.min": "0.1", "snowfall.limits.max": "0.8",
        "snowfall.thresholds.min": "0.35", "snowfall.thresholds.max": "1.0", "snowfall.thresholds.end": "300",
        "storm.density": "0.0",
    },
    "Blizzard": {
        "overcast.current.actual": "0.95", "overcast.limits.min": "0.75", "overcast.limits.max": "1.0",
        "fog.current.actual": "0.2", "fog.limits.max": "0.45",
        "rain.current.actual": "0.0", "rain.limits.max": "0.0",
        "windMagnitude.current.actual": "12.0", "windMagnitude.limits.min": "6.0", "windMagnitude.limits.max": "26.0",
        "snowfall.current.actual": "0.8", "snowfall.limits.min": "0.45", "snowfall.limits.max": "1.0",
        "snowfall.thresholds.min": "0.55", "snowfall.thresholds.max": "1.0", "snowfall.thresholds.end": "120",
        "storm.density": "0.2", "storm.threshold": "0.9", "storm.timeout": "60",
    },
}

WEATHER_PRESET_CATEGORIES = {
    "Spring": ("Spring Clear", "Spring Showers", "Cloudy", "Foggy"),
    "Summer": ("Sunny", "Hot Dry", "Mixed", "Thunder", "Storm"),
    "Autumn": ("Autumn Clear", "Autumn Rain", "Windy Rain", "Rainy", "Foggy"),
    "Winter": ("Winter Clear", "Winter Fog", "Snowy", "Blizzard"),
}


def order_type_entry_children(element: ET.Element) -> None:
    children = list(element)
    if not children:
        return
    indexed = list(enumerate(children))

    def order_key(item: tuple[int, ET.Element]) -> tuple[int, int]:
        index, child = item
        try:
            return (TYPE_CHILD_ORDER.index(str(child.tag)), index)
        except ValueError:
            return (len(TYPE_CHILD_ORDER), index)

    ordered = [child for _index, child in sorted(indexed, key=order_key)]
    if ordered == children:
        return
    element[:] = ordered


def order_event_entry_children(element: ET.Element) -> None:
    children = list(element)
    if not children:
        return
    indexed = list(enumerate(children))

    def order_key(item: tuple[int, ET.Element]) -> tuple[int, int]:
        index, child = item
        try:
            return (EVENT_CHILD_ORDER.index(str(child.tag)), index)
        except ValueError:
            return (len(EVENT_CHILD_ORDER), index)

    ordered = [child for _index, child in sorted(indexed, key=order_key)]
    if ordered != children:
        element[:] = ordered


@dataclass
class TypeEntry:
    name: str
    element: ET.Element
    source_path: str
    source_index: int

    def child_text(self, tag: str, default: str = "") -> str:
        child = self.element.find(tag)
        if child is None or child.text is None:
            return default
        return child.text.strip()

    def set_child_text(self, tag: str, value: str) -> None:
        child = self.element.find(tag)
        if child is None:
            child = ET.SubElement(self.element, tag)
        child.text = str(value).strip()
        order_type_entry_children(self.element)

    def first_relation_name(self, tag: str) -> str:
        child = self.element.find(tag)
        return child.attrib.get("name", "") if child is not None else ""

    def relation_names(self, tag: str) -> list[str]:
        return [child.attrib.get("name", "") for child in self.element.findall(tag) if child.attrib.get("name", "")]

    def set_relation_names_preserve_order(self, tag: str, names: Iterable[str]) -> None:
        values = [name.strip() for name in names if name.strip()]
        children = list(self.element)
        existing = [child for child in children if child.tag == tag]
        if not values:
            for child in existing:
                self.element.remove(child)
            order_type_entry_children(self.element)
            return

        if existing:
            anchor_index = children.index(existing[0])
            for child, value in zip(existing, values):
                child.attrib.clear()
                child.attrib["name"] = value
            for child in existing[len(values) :]:
                self.element.remove(child)
            insert_at = anchor_index + min(len(existing), len(values))
        else:
            insert_at = self.relation_insert_index(tag, children)

        for value in values[len(existing) :]:
            child = self.element.makeelement(tag, {"name": value})
            self.element.insert(insert_at, child)
            insert_at += 1
        order_type_entry_children(self.element)

    def relation_insert_index(self, tag: str, children: list[ET.Element]) -> int:
        if tag in TYPE_RELATION_ORDER:
            relation_index = TYPE_RELATION_ORDER.index(tag)
            later_relation_tags = TYPE_RELATION_ORDER[relation_index + 1 :]
            for index, child in enumerate(children):
                if child.tag in later_relation_tags:
                    return index
            earlier_relation_tags = set(TYPE_RELATION_ORDER[:relation_index])
            for index in range(len(children) - 1, -1, -1):
                if children[index].tag in earlier_relation_tags:
                    return index + 1

        anchor_tags = ("flags", "cost", "quantmax", "quantmin", "min", "restock", "lifetime", "nominal")
        for anchor in anchor_tags:
            for index in range(len(children) - 1, -1, -1):
                if children[index].tag == anchor:
                    return index + 1
        return len(children)

    def clone(self) -> "TypeEntry":
        return TypeEntry(
            name=self.name,
            element=copy.deepcopy(self.element),
            source_path=self.source_path,
            source_index=self.source_index,
        )


@dataclass(frozen=True)
class TypeSplitGroup:
    key: str
    label: str
    filename: str
    entries: tuple[TypeEntry, ...]


@dataclass(frozen=True)
class TypeSplitRule:
    kind: str
    pattern: str
    label: str


@dataclass(frozen=True)
class ConfigClassEntry:
    name: str
    section: str
    source_path: str
    base_class: str = ""
    body: str = ""
    scope: int | None = None
    category_hint: str = ""


@dataclass(frozen=True)
class LootMapGroupSummary:
    name: str
    building_count: int
    spawnpoints_per_building: int
    capacity_per_building: int
    total_spawnpoints: int
    total_capacity: int
    relations: dict[str, tuple[str, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class LootRelationSummary:
    kind: str
    name: str
    nominal: int
    item_count: int
    capacity: int
    spawnpoints: int
    building_count: int

    @property
    def ratio(self) -> float | None:
        if self.capacity <= 0:
            return None
        return self.nominal / self.capacity

    @property
    def status(self) -> str:
        if self.nominal <= 0:
            return "unused"
        if self.capacity <= 0:
            return "no capacity"
        if self.nominal > self.capacity:
            return "over target"
        if self.nominal > self.capacity * 0.85:
            return "tight"
        if self.nominal < self.capacity * 0.15:
            return "very loose"
        return "ok"


@dataclass(frozen=True)
class LootItemRarityRow:
    class_name: str
    nominal: int
    minimum: int
    lifetime: int
    restock: int
    cost: int
    categories: tuple[str, ...]
    usages: tuple[str, ...]
    values: tuple[str, ...]
    tags: tuple[str, ...]
    flags: dict[str, int] = field(default_factory=dict)
    eligible_spawn_points: int = 0
    location_density: float = 0.0
    pool_weight: int = 0
    hoarding_sensitivity: int = 0
    effective_rarity_score: float = 0.0
    findability_score: float = 0.0
    rarity_index: float = 0.0
    estimated_rarity_label: str = ""
    spawn_sources: tuple[str, ...] = ()
    distribution_by_usage: dict[str, int] = field(default_factory=dict)
    distribution_by_tier: dict[str, int] = field(default_factory=dict)
    direct_world_findability: float = 0.0
    event_findability: float = 0.0
    attachment_availability: float = 0.0

    @property
    def category_text(self) -> str:
        return ", ".join(self.categories)

    @property
    def usage_text(self) -> str:
        return ", ".join(self.usages)

    @property
    def value_text(self) -> str:
        return ", ".join(self.values)

    @property
    def tag_text(self) -> str:
        return ", ".join(self.tags)

    @property
    def flag_text(self) -> str:
        return ", ".join(f"{key}={value}" for key, value in self.flags.items())

    @property
    def spawn_source_text(self) -> str:
        return ", ".join(self.spawn_sources)


@dataclass(frozen=True)
class LootDistributionReport:
    mapgroupproto_path: str
    mapgrouppos_path: str
    map_group_count: int
    placed_group_count: int
    total_capacity: int
    total_spawnpoints: int
    total_nominal: int
    relation_summaries: tuple[LootRelationSummary, ...]
    item_rows: tuple[LootItemRarityRow, ...]
    map_group_summaries: tuple[LootMapGroupSummary, ...]
    unmatched_items: tuple[str, ...]
    warnings: tuple[str, ...]
    body: str = ""
    scope: str = ""
    category_hint: str = ""


@dataclass(frozen=True)
class MapGroupProtoPoint:
    pos: str
    range: str = ""
    height: str = ""
    flags: str = ""
    issue_count: int = 0
    commented: bool = False


@dataclass(frozen=True)
class MapGroupProtoContainer:
    name: str
    lootmax: int
    point_count: int
    categories: tuple[str, ...] = ()
    usages: tuple[str, ...] = ()
    values: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    matching_item_count: int = 0
    issue_count: int = 0
    points: tuple[MapGroupProtoPoint, ...] = ()
    commented: bool = False


@dataclass(frozen=True)
class MapGroupProtoGroup:
    name: str
    lootmax: int
    placed_count: int
    container_count: int
    point_count: int
    container_lootmax_sum: int
    categories: tuple[str, ...] = ()
    usages: tuple[str, ...] = ()
    values: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    matching_item_count: int = 0
    issue_count: int = 0
    containers: tuple[MapGroupProtoContainer, ...] = ()
    xml: str = ""
    commented: bool = False


@dataclass
class EventEntry:
    name: str
    element: ET.Element
    source_path: str
    source_index: int

    def child_text(self, tag: str, default: str = "") -> str:
        child = self.element.find(tag)
        if child is None or child.text is None:
            return default
        return child.text.strip()

    def set_child_text(self, tag: str, value: str) -> None:
        child = self.element.find(tag)
        if child is None:
            child = ET.SubElement(self.element, tag)
        child.text = str(value).strip()
        order_event_entry_children(self.element)

    def set_optional_child_text(self, tag: str, value: str) -> None:
        clean = str(value).strip()
        children = self.element.findall(tag)
        if not clean:
            for child in children:
                self.element.remove(child)
            return
        self.set_child_text(tag, clean)

    def flag_value(self, name: str, default: str = "") -> str:
        flags = self.element.find("flags")
        return flags.attrib.get(name, default).strip() if flags is not None else default

    def set_flags(self, values: dict[str, str]) -> None:
        clean = {name: str(values.get(name, "")).strip() for name in EVENT_FLAG_FIELDS}
        flags = self.element.find("flags")
        if not any(clean.values()):
            if flags is not None:
                self.element.remove(flags)
            return
        if flags is None:
            flags = ET.SubElement(self.element, "flags")
        for name, value in clean.items():
            if value:
                flags.attrib[name] = value
            else:
                flags.attrib.pop(name, None)
        order_event_entry_children(self.element)

    def is_enabled(self) -> bool:
        return self.child_text("active", "1") != "0"

    def family(self) -> str:
        return classify_event_name(self.name)[0]

    def link_target(self) -> str:
        return classify_event_name(self.name)[2]

    def clone(self) -> "EventEntry":
        return EventEntry(
            name=self.name,
            element=copy.deepcopy(self.element),
            source_path=self.source_path,
            source_index=self.source_index,
        )


def create_event_entry(
    name: str,
    source_path: str,
    source_index: int = 0,
    field_values: dict[str, str] | None = None,
    flag_values: dict[str, str] | None = None,
    child_attributes: dict[str, str] | None = None,
) -> EventEntry:
    clean_name = str(name or "").strip()
    if not clean_name:
        raise ValueError("Event name cannot be empty.")
    entry = EventEntry(clean_name, ET.Element("event", {"name": clean_name}), str(source_path), int(source_index))
    for field_name in EVENT_CHILD_ORDER:
        if field_name in {"flags", "children"}:
            continue
        value = str((field_values or {}).get(field_name, "")).strip()
        if value:
            entry.set_child_text(field_name, value)
    entry.set_flags(flag_values or {})
    children = ET.SubElement(entry.element, "children")
    child_type = str((child_attributes or {}).get("type", "")).strip()
    if child_type:
        attributes = {
            field_name: str((child_attributes or {}).get(field_name, "")).strip()
            for field_name in ("lootmax", "lootmin", "max", "min", "type")
            if str((child_attributes or {}).get(field_name, "")).strip()
        }
        attributes["type"] = child_type
        ET.SubElement(children, "child", attributes)
    order_event_entry_children(entry.element)
    return entry


@dataclass
class SpawnableItem:
    name: str
    attributes: dict[str, str]
    element: ET.Element


@dataclass
class SpawnableBlock:
    kind: str
    attributes: dict[str, str]
    items: list[SpawnableItem]
    element: ET.Element


@dataclass
class SpawnableTypeEntry:
    name: str
    element: ET.Element
    source_path: str
    source_index: int

    def blocks(self, kind: str | None = None) -> list[SpawnableBlock]:
        wanted = str(kind or "").casefold()
        result: list[SpawnableBlock] = []
        for block in self.element:
            block_kind = str(block.tag or "")
            if block_kind.casefold() not in {"cargo", "attachments"}:
                continue
            if wanted and block_kind.casefold() != wanted:
                continue
            items = [
                SpawnableItem(
                    name=item.attrib.get("name", "").strip(),
                    attributes={str(key): str(value) for key, value in item.attrib.items()},
                    element=copy.deepcopy(item),
                )
                for item in block.findall("item")
            ]
            result.append(
                SpawnableBlock(
                    kind=block_kind,
                    attributes={str(key): str(value) for key, value in block.attrib.items()},
                    items=items,
                    element=copy.deepcopy(block),
                )
            )
        return result

    def cargo_blocks(self) -> list[SpawnableBlock]:
        return self.blocks("cargo")

    def attachment_blocks(self) -> list[SpawnableBlock]:
        return self.blocks("attachments")

    def preset_names(self) -> list[str]:
        values: list[str] = []
        for block in self.element:
            if str(block.tag or "").casefold() not in {"cargo", "attachments"}:
                continue
            preset = block.attrib.get("preset", "").strip()
            if preset:
                values.append(preset)
        return values

    def referenced_item_names(self) -> list[str]:
        values: list[str] = []
        for block in self.element:
            if str(block.tag or "").casefold() not in {"cargo", "attachments"}:
                continue
            for item in block.findall("item"):
                name = item.attrib.get("name", "").strip()
                if name:
                    values.append(name)
        return values

    def block_count(self, kind: str) -> int:
        wanted = str(kind or "").casefold()
        return sum(1 for block in self.element if str(block.tag or "").casefold() == wanted)

    def clone(self) -> "SpawnableTypeEntry":
        return SpawnableTypeEntry(
            name=self.name,
            element=copy.deepcopy(self.element),
            source_path=self.source_path,
            source_index=self.source_index,
        )


@dataclass
class RandomPresetItem:
    name: str
    attributes: dict[str, str]
    element: ET.Element


@dataclass
class RandomPresetEntry:
    name: str
    kind: str
    attributes: dict[str, str]
    items: list[RandomPresetItem]
    element: ET.Element
    source_path: str
    source_index: int

    def referenced_item_names(self) -> list[str]:
        return [item.name for item in self.items if item.name]

    def clone(self) -> "RandomPresetEntry":
        return RandomPresetEntry(
            name=self.name,
            kind=self.kind,
            attributes=dict(self.attributes),
            items=[
                RandomPresetItem(
                    name=item.name,
                    attributes=dict(item.attributes),
                    element=copy.deepcopy(item.element),
                )
                for item in self.items
            ],
            element=copy.deepcopy(self.element),
            source_path=self.source_path,
            source_index=self.source_index,
        )


@dataclass
class EventSpawnZone:
    attributes: dict[str, str]
    element: ET.Element

    def clone(self) -> "EventSpawnZone":
        return EventSpawnZone(dict(self.attributes), copy.deepcopy(self.element))


@dataclass
class EventSpawnPosition:
    event_name: str
    attributes: dict[str, str]
    source_path: str
    source_index: int
    element: ET.Element | None = None

    def coordinate_preview(self) -> str:
        parts = []
        for key in ("x", "y", "z", "a"):
            value = self.attributes.get(key)
            if value is not None:
                parts.append(f"{key}={value}")
        group = self.attributes.get("group", "").strip()
        if group:
            parts.append(f"group={group}")
        return ", ".join(parts)

    def set_attribute(self, key: str, value: str) -> None:
        clean = str(value).strip()
        if clean:
            self.attributes[key] = clean
            if self.element is not None:
                self.element.attrib[key] = clean
        else:
            self.attributes.pop(key, None)
            if self.element is not None:
                self.element.attrib.pop(key, None)

    def clone(self) -> "EventSpawnPosition":
        return EventSpawnPosition(
            self.event_name,
            dict(self.attributes),
            self.source_path,
            self.source_index,
            copy.deepcopy(self.element) if self.element is not None else None,
        )


@dataclass
class TerritoryZone:
    name: str
    attributes: dict[str, str]
    element: ET.Element
    source_path: str
    source_index: int
    group_index: int = 0
    group_name: str = ""
    group_attributes: dict[str, str] = field(default_factory=dict)

    def coordinate_preview(self) -> str:
        parts = []
        for key in ("x", "z", "r", "dmin", "dmax"):
            value = self.attributes.get(key)
            if value is not None:
                parts.append(f"{key}={value}")
        return ", ".join(parts)

    def set_attribute(self, key: str, value: str) -> None:
        clean_value = str(value).strip()
        if key == "name":
            self.name = clean_value or "<missing territory zone name>"
        if clean_value:
            self.attributes[key] = clean_value
            self.element.attrib[key] = clean_value
        else:
            self.attributes.pop(key, None)
            self.element.attrib.pop(key, None)

    def clone(self) -> "TerritoryZone":
        return TerritoryZone(
            name=self.name,
            attributes=dict(self.attributes),
            element=copy.deepcopy(self.element),
            source_path=self.source_path,
            source_index=self.source_index,
            group_index=self.group_index,
            group_name=self.group_name,
            group_attributes=dict(self.group_attributes),
        )

    def group_display_name(self) -> str:
        return self.group_name.strip() or f"Group {self.group_index + 1}"


@dataclass
class TerritoryGroup:
    source_path: str
    source_index: int
    group_index: int
    name: str = ""
    attributes: dict[str, str] = field(default_factory=dict)
    zone_count: int = 0

    def clone(self) -> "TerritoryGroup":
        return TerritoryGroup(
            source_path=self.source_path,
            source_index=self.source_index,
            group_index=self.group_index,
            name=self.name,
            attributes=dict(self.attributes),
            zone_count=self.zone_count,
        )

    def display_name(self) -> str:
        return self.name.strip() or f"Group {self.group_index + 1}"


@dataclass
class CEZoneLayer:
    name: str
    kind: str
    usage_flags: int
    value_flags: int
    color: int
    visible: bool
    source_path: str
    image_path: str = ""
    attributes: dict[str, str] = field(default_factory=dict)

    def display_name(self) -> str:
        if self.kind == "value":
            return self.name.removeprefix("valueFlg_")
        if self.kind == "usage_paint":
            return self.name.removeprefix("usgFlg_Paint-")
        if self.kind == "usage_default":
            return self.name.removeprefix("usgFlg_Def-")
        return self.name


@dataclass
class CEZoneProject:
    source_path: str
    map_file: str
    layer_size: int
    world_size: int
    usages: list[str]
    values: list[str]
    layers: list[CEZoneLayer]
    issues: list[ValidationIssue] = field(default_factory=list)

    def layer_groups(self) -> dict[str, list[CEZoneLayer]]:
        groups: dict[str, list[CEZoneLayer]] = {
            "Value tiers": [],
            "Usage paint": [],
            "Usage defaults": [],
            "Water and points": [],
            "Other": [],
        }
        for layer in self.layers:
            if layer.kind == "value":
                groups["Value tiers"].append(layer)
            elif layer.kind == "usage_paint":
                groups["Usage paint"].append(layer)
            elif layer.kind == "usage_default":
                groups["Usage defaults"].append(layer)
            elif layer.kind in {"water", "keypoint", "attractors"}:
                groups["Water and points"].append(layer)
            else:
                groups["Other"].append(layer)
        return groups


@dataclass(frozen=True)
class AreaFlagsMap:
    source_path: str
    width: int
    height: int
    world_x: int
    world_z: int
    cell_size: int
    reserved: int
    usage_planes: tuple[bytes, bytes, bytes, bytes]
    value_plane: bytes

    @property
    def pixel_count(self) -> int:
        return self.width * self.height

    def value_mask(self, value_flags: int, y_flip: bool = True) -> bytes:
        return bit_mask_from_plane(self.value_plane, self.width, self.height, int(value_flags), y_flip=y_flip)

    def usage_mask(self, usage_flags: int, y_flip: bool = True) -> bytes:
        return bit_mask_from_usage_planes(self.usage_planes, self.width, self.height, int(usage_flags), y_flip=y_flip)


@dataclass
class EventSpawnGroup:
    name: str
    positions: list[EventSpawnPosition]
    element: ET.Element
    source_path: str
    source_index: int
    zone: EventSpawnZone | None = None

    def clone(self) -> "EventSpawnGroup":
        return EventSpawnGroup(
            self.name,
            [position.clone() for position in self.positions],
            copy.deepcopy(self.element),
            self.source_path,
            self.source_index,
            self.zone.clone() if self.zone is not None else None,
        )

    def grouped_position_count(self) -> int:
        return sum(1 for position in self.positions if position.attributes.get("group", "").strip())


@dataclass
class EventGroupChild:
    type_name: str
    attributes: dict[str, str]
    element: ET.Element

    def set_attribute(self, key: str, value: str) -> None:
        clean = str(value).strip()
        if clean:
            self.attributes[key] = clean
            self.element.attrib[key] = clean
        else:
            self.attributes.pop(key, None)
            self.element.attrib.pop(key, None)
        if key == "type":
            self.type_name = clean

    def clone(self) -> "EventGroupChild":
        return EventGroupChild(self.type_name, dict(self.attributes), copy.deepcopy(self.element))


@dataclass
class EventGroupDefinition:
    name: str
    children: list[EventGroupChild]
    element: ET.Element
    source_path: str
    source_index: int

    def clone(self) -> "EventGroupDefinition":
        return EventGroupDefinition(
            self.name,
            [child.clone() for child in self.children],
            copy.deepcopy(self.element),
            self.source_path,
            self.source_index,
        )

    def max_offset_radius(self) -> float:
        radii = []
        for child in self.children:
            try:
                radii.append(math.hypot(float(child.attributes.get("x", "0")), float(child.attributes.get("z", "0"))))
            except ValueError:
                continue
        return max(radii, default=0.0)


@dataclass(frozen=True)
class EventSecondaryLink:
    parent_name: str
    secondary_name: str
    parent_source_path: str
    parent_enabled: bool


@dataclass
class ValidationIssue:
    severity: str
    name: str
    message: str
    source_path: str = ""
    suggestion: str = ""
    line: int | None = None
    column: int | None = None
    context: str = ""


@dataclass
class DayZLogFinding:
    severity: str
    title: str
    message: str
    source_path: str = ""
    line: int | None = None
    context: str = ""
    suggestion: str = ""
    reason: str = ""
    script_class: str = ""
    crash_function_name: str = ""
    mod_name: str = ""
    script_file: str = ""
    function_name: str = ""
    row: int | None = None
    location: str = ""
    related_name: str = ""
    crash_phase: str = ""
    stack_frames: list[dict[str, object]] = field(default_factory=list)


@dataclass
class DayZLogAnalysis:
    files: list[str]
    findings: list[DayZLogFinding]
    counters: dict[str, list[tuple[str, int]]]
    session_events: list[str]

    def counts_by_severity(self) -> dict[str, int]:
        counts: Counter[str] = Counter(finding.severity for finding in self.findings)
        return {severity: counts.get(severity, 0) for severity in ("error", "warning", "hint")}

    def to_text(self) -> str:
        counts = self.counts_by_severity()
        scanned_label = "file" if len(self.files) == 1 else "files"
        lines = [
            "RaG Economy Manager Logs Analyzer report",
            "",
            "Summary",
            "-------",
            f"- Files scanned: {len(self.files)} selected {scanned_label}",
            f"- Issues found: {counts['error']} error(s), {counts['warning']} warning(s), {counts['hint']} hint(s)",
        ]
        primary = self.primary_finding()
        if primary is not None:
            lines.extend(self.primary_summary_lines(primary))
            native_crash = next((finding for finding in self.findings if finding is not primary and ("native crash" in finding.title.casefold() or "heap" in finding.title.casefold() or "minidump" in finding.title.casefold())), None)
            if native_crash is not None:
                lines.append(f"- Also found: {native_crash.title}. Treat this as secondary until the script error above is fixed.")
            action = primary.suggestion or self.default_suggestion(primary)
            if action:
                lines.append(f"- First action: {action}")
        lines.append("")
        if self.files:
            lines.append("Selected File(s)")
            lines.append("----------------")
            for path in self.files:
                lines.append(f"- {path}")
            lines.append("")

        if self.session_events:
            lines.append("Session Timeline")
            lines.append("----------------")
            for event in self.session_events:
                lines.append(f"- {event}")
            lines.append("")

        if self.findings:
            lines.append("Issues Found")
            lines.append("------------")
            for number, finding in enumerate(self.findings, start=1):
                location = ""
                if finding.source_path:
                    location = f"{short_source(finding.source_path)}"
                    if finding.line is not None:
                        location += f":{finding.line}"
                lines.append(f"Issue {number}: {finding.title}")
                lines.append(f"- Severity: {finding.severity.upper()}")
                if location:
                    lines.append(f"- Log location: {location}")
                lines.append(f"- What happened: {finding.message}")
                source = self.source_summary(finding)
                if source:
                    lines.append(f"- Likely source: {source}")
                if finding.location:
                    lines.append(f"- Exact script path: {finding.location}")
                if finding.crash_phase:
                    lines.append(f"- Timing: {finding.crash_phase}")
                suggestion = finding.suggestion or self.default_suggestion(finding)
                if suggestion:
                    lines.append(f"- What to do: {suggestion}")
                stack_lines = self.stack_summary_lines(finding)
                if stack_lines:
                    lines.append("- Relevant stack:")
                    lines.extend(f"  {line}" for line in stack_lines)
                elif finding.context:
                    lines.append("- Evidence:")
                    for context_line in finding.context.splitlines()[:6]:
                        lines.append(f"  {context_line}")
                lines.append("")
        else:
            lines.append("Issues Found")
            lines.append("------------")
            lines.append("- No obvious crash, script exception, or economy spam signatures found.")
            lines.append("")

        counter_titles = {
            "search_overtime": "CE Search Overtime Items",
            "hard_to_place": "Hard-To-Place Items",
            "lootmax_mismatch": "Static Loot Max Mismatches",
        }
        for key, title in counter_titles.items():
            rows = self.counters.get(key, [])
            if not rows:
                continue
            lines.append(title)
            lines.append("-" * len(title))
            for name, count in rows[:25]:
                lines.append(f"- {name}: {count}")
            lines.append("")

        lines.append("How To Read This")
        lines.append("----------------")
        lines.append("- A DayZ crash log is a call story: bottom of the stack is where the action started, top is where it crashed.")
        lines.append("- The top vanilla function often only shows where it exploded.")
        lines.append("- The most useful source hint is usually the first modded line below the vanilla lines.")
        lines.append("- Other modded lines below that are context, not proof that those mods caused the error.")
        lines.append("- Native heap crashes like C0000374 often happen after the real bad write. Compare with script errors just before it.")
        return "\n".join(lines).rstrip() + "\n"

    @staticmethod
    def primary_summary_lines(finding: DayZLogFinding) -> list[str]:
        title = finding.title.casefold()
        lines = []
        if "script vm exception" in title or "script null pointer" in title:
            lines.append("- Result: Script crash. This is actionable.")
            if finding.reason:
                lines.append(f"- Reason: {finding.reason}")
            crash_function = finding.crash_function_name or finding.function_name
            if finding.script_class or crash_function:
                class_part = f"class {finding.script_class}" if finding.script_class else "unknown class"
                function_part = f"function {crash_function}" if crash_function else "unknown function"
                lines.append(f"- Crashed in: {class_part}, {function_part}")
            source = DayZLogAnalysis.source_summary(finding)
            if source:
                lines.append(f"- Likely source: {source}")
            if finding.crash_phase:
                lines.append(f"- Timing: {finding.crash_phase}")
            return lines
        lines.append(f"- Result: {DayZLogAnalysis.hoster_summary(finding)}")
        source = DayZLogAnalysis.source_summary(finding)
        if source:
            lines.append(f"- Most likely source: {source}")
        return lines

    @staticmethod
    def stack_summary_lines(finding: DayZLogFinding) -> list[str]:
        if not finding.stack_frames:
            return []
        lines = []
        culprit_key = (finding.location, finding.row, finding.function_name)
        for frame in finding.stack_frames[:8]:
            marker = "->" if (frame.get("path"), frame.get("row"), frame.get("function")) == culprit_key else "  "
            mod = frame.get("mod") or "Vanilla DayZ"
            file_name = frame.get("file") or ""
            row = frame.get("row")
            function = frame.get("function") or ""
            lines.append(f"{marker} {mod}: {file_name}:{row} {function}".rstrip())
        if len(finding.stack_frames) > 8:
            lines.append(f"   ... {len(finding.stack_frames) - 8} more stack frame(s)")
        return lines

    def primary_finding(self) -> DayZLogFinding | None:
        for severity in ("error", "warning", "hint"):
            finding = next((item for item in self.findings if item.severity == severity), None)
            if finding is not None:
                return finding
        return None

    def easy_summary(self) -> str:
        counts = self.counts_by_severity()
        if counts["error"]:
            first_error = next((finding for finding in self.findings if finding.severity == "error"), None)
            if first_error:
                return self.hoster_summary(first_error)
            return "The selected file needs attention. One or more serious issues were detected."
        if counts["warning"]:
            first_warning = next((finding for finding in self.findings if finding.severity == "warning"), None)
            if first_warning:
                return self.hoster_summary(first_warning)
            return "No hard crash was proven, but warnings were detected."
        return "No obvious crash signature, script exception, or economy spam was detected in the selected file."

    @staticmethod
    def source_summary(finding: DayZLogFinding) -> str:
        parts = []
        if finding.mod_name:
            parts.append(f"mod {finding.mod_name}")
        if finding.script_file:
            parts.append(f"file {finding.script_file}")
        if finding.function_name:
            parts.append(f"function {finding.function_name}")
        if finding.row is not None:
            parts.append(f"row {finding.row}")
        if finding.related_name:
            parts.append(f"entry {finding.related_name}")
        return ", ".join(parts)

    @staticmethod
    def hoster_summary(finding: DayZLogFinding) -> str:
        title = finding.title.casefold()
        source = DayZLogAnalysis.source_summary(finding)
        if "script vm exception" in title or "script null pointer" in title:
            return f"A server script crashed. {('Likely source: ' + source + '. ') if source else ''}{finding.message}"
        if "heap" in title or "native crash" in title or "minidump exception" in title:
            return f"The DayZ server process crashed natively. {finding.message}"
        if "search overtime" in title:
            return f"The economy is repeatedly trying and failing to place loot. {finding.message}"
        if "hard-to-place" in title:
            return f"The economy is struggling to place loot and can cause lag. {finding.message}"
        if "loot max mismatch" in title:
            return f"A static/event loot setup has impossible child loot limits. {finding.message}"
        if "empty file path" in title:
            return f"A mod or config tried to read an empty file path. {finding.message}"
        return f"{finding.title}. {finding.message}"

    @staticmethod
    def default_suggestion(finding: DayZLogFinding) -> str:
        title = finding.title.casefold()
        message = finding.message.casefold()
        if "script vm exception" in title or "script null pointer" in title:
            return "Open the referenced script/mod file and fix the null object or failing function named in the evidence."
        if "heap" in title or "c0000374" in title or "heap corruption" in message:
            return "Run Deep Minidump on the matching .mdmp file and compare the timestamp with script/RPT errors before the crash."
        if "native crash" in title or "minidump exception" in title:
            return "Use the matching RPT plus Deep Minidump output. Without Bohemia private symbols, the exact engine function may stay unknown."
        if "search overtime" in title:
            return "Check the listed loot type's category/usage/value and spawn locations. It probably has too few valid places to spawn."
        if "hard-to-place" in title:
            return "Reduce bad placement pressure: review item size, usage flags, mapgroups, and nearby spawn density."
        if "loot max mismatch" in title:
            return "Fix cfgspawnabletypes/random presets so child loot amounts do not exceed the container/event capacity."
        if "empty file path" in title:
            return "Find the mod/config writing an empty path and set a real file path or disable that feature."
        return ""


@dataclass
class DayZDebuggerResult:
    dump_path: str
    debugger_path: str
    success: bool
    timed_out: bool
    output: str
    error: str = ""

    def highlights(self) -> list[str]:
        return extract_dayz_debugger_highlights(self.output)

    def to_text(self) -> str:
        lines = [
            "Easy summary:",
            self.easy_summary(),
            "",
            "Detailed summary:",
            f"- Minidump: {self.dump_path}",
            f"- Debugger: {self.debugger_path or 'not found'}",
        ]
        if self.timed_out:
            lines.append("- Status: timed out")
        elif self.success:
            lines.append("- Status: complete")
        else:
            lines.append("- Status: failed")
        if self.error:
            lines.append(f"- Error: {self.error}")
        highlights = self.highlights()
        if highlights:
            lines.append("")
            lines.append("Possible issues:")
            lines.extend(f"- {line}" for line in highlights)
        return "\n".join(lines).rstrip() + "\n"

    def easy_summary(self) -> str:
        if self.timed_out:
            return "The debugger took too long and did not finish. Try again with fewer selected dumps."
        if not self.success:
            return self.error or "The debugger could not analyze this dump."
        highlights = self.highlights()
        joined = "\n".join(highlights).casefold()
        if "c0000374" in joined or "heap" in joined:
            return "The server crashed because heap memory was corrupted. The visible crash point is usually where DayZ noticed the corruption, not always where it was caused."
        if "c0000005" in joined or "access violation" in joined:
            return "The server crashed from an access violation. Something tried to read or write invalid memory."
        if "dayzserver_x64+" in joined:
            return "The dump contains DayZ server stack frames, but private Bohemia symbols are needed to translate most engine addresses into readable function names."
        return "The debugger completed and extracted crash metadata. Review the highlights below."


@dataclass
class CompareChange:
    name: str
    change_type: str
    fields: list[str]


@dataclass
class MissionWorkspace:
    root_path: str
    type_paths: list[str]
    spawnable_type_paths: list[str]
    random_preset_paths: list[str]
    event_paths: list[str]
    event_spawn_paths: list[str]
    event_group_paths: list[str]
    territory_paths: list[str]
    cfgenvironment_paths: list[str]
    environment_territory_paths: dict[str, list[str]]
    cfgeconomycore_paths: list[str]
    cfglimits_paths: list[str]
    relation_definitions: dict[str, list[str]]
    issues: list[ValidationIssue]

    def relation_count(self) -> int:
        return sum(len(values) for values in self.relation_definitions.values())


def parse_types_file(path: str | os.PathLike[str], source_index: int = 0) -> tuple[list[TypeEntry], list[ValidationIssue]]:
    path = str(path)
    issues: list[ValidationIssue] = []

    try:
        parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
        tree = parse_xml_file(path, parser=parser)
    except ET.ParseError as exc:
        return [], [create_xml_parse_issue(path, exc)]
    except OSError as exc:
        return [], [ValidationIssue("error", "", f"Could not read file: {exc}", path, "Check that the file exists, is not locked by another program, and that you have permission to read it.")]

    root = tree.getroot()
    if root.tag != "types":
        issues.append(ValidationIssue("error", "", f"Expected root <types>, got <{root.tag}>", path, "This file does not match the current types.xml editor module. Load a types.xml-style file or wait for the matching file module."))

    entries: list[TypeEntry] = []
    for index, element in enumerate(root.findall("type")):
        name = element.attrib.get("name", "").strip()
        if not name:
            name = f"<missing name #{index + 1}>"
            issues.append(ValidationIssue("error", name, "Type entry is missing the name attribute.", path, "Add a classname, for example <type name=\"Your_Classname\">."))
        entries.append(TypeEntry(name=name, element=copy.deepcopy(element), source_path=path, source_index=source_index))

    if not entries and root.tag == "types":
        issues.append(ValidationIssue("warning", "", "No <type> entries found.", path, "Check that you loaded the intended file for the current types.xml editor module."))

    issues.extend(validate_entries(entries, include_duplicates=False))
    return entries, issues


def parse_spawnable_types_file(path: str | os.PathLike[str], source_index: int = 0) -> tuple[list[SpawnableTypeEntry], list[ValidationIssue]]:
    path = str(path)
    issues: list[ValidationIssue] = []

    try:
        tree = parse_xml_file(path)
    except ET.ParseError as exc:
        return [], [create_xml_parse_issue(path, exc)]
    except OSError as exc:
        return [], [ValidationIssue("error", "", f"Could not read file: {exc}", path, "Check that the file exists, is not locked by another program, and that you have permission to read it.")]

    root = tree.getroot()
    if root.tag != "spawnabletypes":
        issues.append(ValidationIssue("error", "", f"Expected root <spawnabletypes>, got <{root.tag}>", path, "This file does not match cfgspawnabletypes.xml. Load a DayZ spawnable types file."))

    entries: list[SpawnableTypeEntry] = []
    for index, element in enumerate(root.findall("type")):
        name = element.attrib.get("name", "").strip()
        if not name:
            name = f"<missing spawnable type name #{index + 1}>"
            issues.append(ValidationIssue("error", name, "Spawnable type entry is missing the name attribute.", path, "Add a classname, for example <type name=\"Weapon_AK74\">."))
        entries.append(SpawnableTypeEntry(name=name, element=copy.deepcopy(element), source_path=path, source_index=source_index))

    if not entries and root.tag == "spawnabletypes":
        issues.append(ValidationIssue("warning", "", "No <type> entries found.", path, "Check that you loaded the intended cfgspawnabletypes.xml file."))

    return entries, issues


def parse_random_presets_file(path: str | os.PathLike[str], source_index: int = 0) -> tuple[list[RandomPresetEntry], list[ValidationIssue]]:
    path = str(path)
    issues: list[ValidationIssue] = []

    try:
        tree = parse_xml_file(path)
    except ET.ParseError as exc:
        return [], [create_xml_parse_issue(path, exc)]
    except OSError as exc:
        return [], [ValidationIssue("error", "", f"Could not read file: {exc}", path, "Check that the file exists, is not locked by another program, and that you have permission to read it.")]

    root = tree.getroot()
    if root.tag != "randompresets":
        issues.append(ValidationIssue("error", "", f"Expected root <randompresets>, got <{root.tag}>", path, "This file does not match cfgrandompresets.xml. Load a DayZ random presets file."))

    entries: list[RandomPresetEntry] = []
    for index, element in enumerate(list(root)):
        if element.tag.casefold() not in {"cargo", "attachments"}:
            continue
        name = element.attrib.get("name", "").strip()
        if not name:
            name = f"<missing random preset name #{index + 1}>"
            issues.append(ValidationIssue("error", name, "Random preset entry is missing the name attribute.", path, "Add a preset name, for example <cargo name=\"FoodPreset\" chance=\"1.00\">."))
        items = [
            RandomPresetItem(
                name=item.attrib.get("name", "").strip(),
                attributes={str(key): str(value) for key, value in item.attrib.items()},
                element=copy.deepcopy(item),
            )
            for item in element.findall("item")
        ]
        entries.append(
            RandomPresetEntry(
                name=name,
                kind=element.tag,
                attributes={str(key): str(value) for key, value in element.attrib.items()},
                items=items,
                element=copy.deepcopy(element),
                source_path=path,
                source_index=source_index,
            )
        )

    if not entries and root.tag == "randompresets":
        issues.append(ValidationIssue("warning", "", "No <cargo> or <attachments> presets found.", path, "Check that you loaded the intended cfgrandompresets.xml file."))

    return entries, issues


def parse_events_file(path: str | os.PathLike[str], source_index: int = 0) -> tuple[list[EventEntry], list[ValidationIssue]]:
    path = str(path)
    issues: list[ValidationIssue] = []

    try:
        parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
        tree = parse_xml_file(path, parser=parser)
    except ET.ParseError as exc:
        return [], [create_xml_parse_issue(path, exc)]
    except OSError as exc:
        return [], [ValidationIssue("error", "", f"Could not read file: {exc}", path, "Check that the file exists, is not locked by another program, and that you have permission to read it.")]

    root = tree.getroot()
    if root.tag != "events":
        issues.append(ValidationIssue("error", "", f"Expected root <events>, got <{root.tag}>", path, "This file does not match the Events module. Load an events.xml-style file."))

    entries: list[EventEntry] = []
    for index, element in enumerate(root.findall("event")):
        name = element.attrib.get("name", "").strip()
        if not name:
            name = f"<missing event name #{index + 1}>"
            issues.append(ValidationIssue("error", name, "Event entry is missing the name attribute.", path, "Add an event name, for example <event name=\"StaticHeliCrash\">."))
        entries.append(EventEntry(name=name, element=copy.deepcopy(element), source_path=path, source_index=source_index))

    if not entries and root.tag == "events":
        issues.append(ValidationIssue("warning", "", "No <event> entries found.", path, "Check that you loaded the intended events.xml file."))

    issues.extend(validate_event_entries(entries, check_references=False))

    return entries, issues


def parse_event_spawns_file(path: str | os.PathLike[str], source_index: int = 0) -> tuple[list[EventSpawnGroup], list[ValidationIssue]]:
    path = str(path)
    issues: list[ValidationIssue] = []

    try:
        parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
        tree = parse_xml_file(path, parser=parser)
    except ET.ParseError as exc:
        return [], [create_xml_parse_issue(path, exc)]
    except OSError as exc:
        return [], [ValidationIssue("error", "", f"Could not read file: {exc}", path, "Check that the file exists, is not locked by another program, and that you have permission to read it.")]

    root = tree.getroot()
    if root.tag != "eventposdef":
        issues.append(ValidationIssue("error", "", f"Expected root <eventposdef>, got <{root.tag}>", path, "This file does not match cfgeventspawns.xml. Load a DayZ event position definition file."))

    groups: list[EventSpawnGroup] = []
    name_counts: Counter = Counter()
    for index, element in enumerate(root.findall("event")):
        name = element.attrib.get("name", "").strip()
        if not name:
            name = f"<missing spawn event name #{index + 1}>"
            issues.append(ValidationIssue("error", name, "Event spawn entry is missing the name attribute.", path, "Add an event name, for example <event name=\"StaticHeliCrash\">."))
        else:
            name_counts[name.casefold()] += 1

        zone_element = element.find("zone")
        zone = None
        if zone_element is not None:
            zone_attributes = {str(key): str(value) for key, value in zone_element.attrib.items()}
            zone = EventSpawnZone(zone_attributes, copy.deepcopy(zone_element))
            zone_values: dict[str, float] = {}
            for field_name in ("smin", "smax", "dmin", "dmax", "r"):
                raw = zone_attributes.get(field_name, "").strip()
                if not raw:
                    continue
                try:
                    value = float(raw)
                    if not math.isfinite(value) or value < 0:
                        raise ValueError
                except ValueError:
                    issues.append(ValidationIssue("error", name, f"Event spawn zone {field_name} must be a non-negative number, got {raw!r}.", path, "Use zero or a positive number."))
                    continue
                zone_values[field_name] = value
            for minimum_name, maximum_name in (("smin", "smax"), ("dmin", "dmax")):
                if minimum_name in zone_values and maximum_name in zone_values and zone_values[minimum_name] > zone_values[maximum_name]:
                    issues.append(ValidationIssue("error", name, f"Event spawn zone {minimum_name} exceeds {maximum_name}.", path, f"Set {minimum_name} less than or equal to {maximum_name}."))
            if zone_values.get("r") == 0 and any(zone_values.get(field_name, 0) > 0 for field_name in ("smin", "smax", "dmin", "dmax")):
                issues.append(ValidationIssue("warning", name, "Event spawn zone has radius 0 with non-zero population counts.", path, "Verify the zone radius; zero can prevent the generated zone from covering a useful area."))

        positions: list[EventSpawnPosition] = []
        for position_index, position in enumerate(element.findall("pos")):
            attributes = {str(key): str(value) for key, value in position.attrib.items()}
            for field_name in ("x", "z"):
                raw = attributes.get(field_name, "").strip()
                if not raw:
                    issues.append(ValidationIssue("error", name, f"Spawn position #{position_index + 1} is missing required {field_name}.", path, "Set both x and z world coordinates."))
                    continue
                try:
                    value = float(raw)
                    if not math.isfinite(value):
                        raise ValueError
                except ValueError:
                    issues.append(ValidationIssue("error", name, f"Spawn position #{position_index + 1} has invalid {field_name}: {raw!r}.", path, "Use a finite numeric coordinate."))
            for field_name in ("y", "a"):
                raw = attributes.get(field_name, "").strip()
                if not raw:
                    continue
                try:
                    value = float(raw)
                    if not math.isfinite(value):
                        raise ValueError
                except ValueError:
                    issues.append(ValidationIssue("error", name, f"Spawn position #{position_index + 1} has invalid {field_name}: {raw!r}.", path, "Use a finite number or omit the optional attribute."))
            positions.append(EventSpawnPosition(name, attributes, path, source_index, copy.deepcopy(position)))
        groups.append(EventSpawnGroup(name, positions, copy.deepcopy(element), path, source_index, zone))

    for group in groups:
        if not group.name.startswith("<missing ") and name_counts[group.name.casefold()] > 1:
            issues.append(ValidationIssue("warning", group.name, "Event spawn name is defined more than once.", path, "Keep one definition unless multiple physical sources intentionally contribute positions."))

    if not groups and root.tag == "eventposdef":
        issues.append(ValidationIssue("warning", "", "No <event> spawn entries found.", path, "Check that you loaded the intended cfgeventspawns.xml file."))

    return groups, issues


def parse_event_groups_file(
    path: str | os.PathLike[str],
    source_index: int = 0,
    known_classnames: Iterable[str] = (),
) -> tuple[list[EventGroupDefinition], list[ValidationIssue]]:
    path = str(path)
    issues: list[ValidationIssue] = []
    try:
        parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
        tree = parse_xml_file(path, parser=parser)
    except ET.ParseError as exc:
        return [], [create_xml_parse_issue(path, exc)]
    except OSError as exc:
        return [], [ValidationIssue("error", "", f"Could not read file: {exc}", path, "Check that the file exists and can be read.")]

    root = tree.getroot()
    if root.tag != "eventgroupdef":
        issues.append(ValidationIssue("error", "", f"Expected root <eventgroupdef>, got <{root.tag}>", path, "This file does not match cfgeventgroups.xml."))

    known = {str(name).casefold() for name in known_classnames if str(name).strip()}
    name_counts: Counter = Counter()
    groups: list[EventGroupDefinition] = []
    for group_index, element in enumerate(root.findall("group")):
        name = element.attrib.get("name", "").strip()
        if not name:
            name = f"<missing event group name #{group_index + 1}>"
            issues.append(ValidationIssue("error", name, "Event group is missing its name attribute.", path, "Add a unique group name."))
        else:
            name_counts[name.casefold()] += 1
        children: list[EventGroupChild] = []
        for child_index, child_element in enumerate(element.findall("child")):
            attributes = {str(key): str(value) for key, value in child_element.attrib.items()}
            type_name = attributes.get("type", "").strip()
            label = type_name or f"child #{child_index + 1}"
            if not type_name:
                issues.append(ValidationIssue("error", name, f"Event group {label} is missing its type classname.", path, "Set type to the entity classname."))
            elif known and type_name.casefold() not in known:
                issues.append(ValidationIssue("warning", name, f"Event group child classname is not loaded: {type_name}.", path, "Keep it if a mod supplies this class; otherwise correct the classname."))
            for field_name in ("x", "z"):
                raw = attributes.get(field_name, "").strip()
                if not raw:
                    issues.append(ValidationIssue("error", name, f"Event group {label} is missing required {field_name} offset.", path, "Set both x and z relative offsets."))
                    continue
                try:
                    value = float(raw)
                    if not math.isfinite(value):
                        raise ValueError
                except ValueError:
                    issues.append(ValidationIssue("error", name, f"Event group {label} has invalid {field_name}: {raw!r}.", path, "Use a finite numeric offset."))
            for field_name in ("y", "a"):
                raw = attributes.get(field_name, "").strip()
                if not raw:
                    continue
                try:
                    value = float(raw)
                    if not math.isfinite(value):
                        raise ValueError
                except ValueError:
                    issues.append(ValidationIssue("error", name, f"Event group {label} has invalid {field_name}: {raw!r}.", path, "Use a finite number or omit the optional attribute."))
            loot_values: dict[str, int] = {}
            for field_name in ("lootmin", "lootmax"):
                raw = attributes.get(field_name, "").strip()
                if not raw:
                    continue
                try:
                    value = int(raw)
                    if value < 0:
                        raise ValueError
                except ValueError:
                    issues.append(ValidationIssue("error", name, f"Event group {label} {field_name} must be a non-negative integer, got {raw!r}.", path, "Use zero or a positive integer."))
                    continue
                loot_values[field_name] = value
            if loot_values.get("lootmin", 0) > loot_values.get("lootmax", loot_values.get("lootmin", 0)):
                issues.append(ValidationIssue("error", name, f"Event group {label} lootmin exceeds lootmax.", path, "Set lootmin less than or equal to lootmax."))
            deloot = attributes.get("deloot", "").strip()
            if deloot and deloot not in {"0", "1"}:
                issues.append(ValidationIssue("error", name, f"Event group {label} deloot must be 0 or 1, got {deloot!r}.", path, "Use 0, 1, or omit the attribute."))
            spawnsecondary = attributes.get("spawnsecondary", "").strip().casefold()
            if spawnsecondary and spawnsecondary not in {"true", "false", "0", "1"}:
                issues.append(ValidationIssue("warning", name, f"Event group {label} has unknown spawnsecondary value: {spawnsecondary!r}.", path, "Use true/false, 0/1, or omit the attribute."))
            children.append(EventGroupChild(type_name, attributes, copy.deepcopy(child_element)))
        groups.append(EventGroupDefinition(name, children, copy.deepcopy(element), path, source_index))

    for group in groups:
        if not group.name.startswith("<missing ") and name_counts[group.name.casefold()] > 1:
            issues.append(ValidationIssue("error", group.name, "Event group name is defined more than once.", path, "Rename or remove duplicate group definitions so references are unambiguous."))
    if not groups and root.tag == "eventgroupdef":
        issues.append(ValidationIssue("warning", "", "No <group> entries found.", path, "Empty cfgeventgroups.xml is valid but provides no grouped layouts."))
    return groups, issues


def parse_territory_file(path: str | os.PathLike[str], source_index: int = 0) -> tuple[list[TerritoryZone], list[ValidationIssue]]:
    path = str(path)
    issues: list[ValidationIssue] = []
    try:
        tree = parse_xml_file(path)
    except ET.ParseError as exc:
        return [], [create_xml_parse_issue(path, exc)]
    except OSError as exc:
        return [], [ValidationIssue("error", "", f"Could not read file: {exc}", path, "Check that the file exists, is not locked by another program, and that you have permission to read it.")]

    root = tree.getroot()
    if root.tag != "territory-type":
        issues.append(ValidationIssue("error", "", f"Expected root <territory-type>, got <{root.tag}>", path, "This file does not match DayZ territory XML. Load an env/*_territories.xml file."))

    zones: list[TerritoryZone] = []

    territory_groups = root.findall("territory")
    if territory_groups:
        grouped_zone_sources = [
            (
                group_index,
                group.attrib.get("name", "").strip(),
                {str(key): str(value) for key, value in group.attrib.items()},
                group.findall("zone"),
            )
            for group_index, group in enumerate(territory_groups)
        ]
    else:
        direct_zones = root.findall("zone")
        if not direct_zones:
            direct_zones = root.findall(".//zone")
        grouped_zone_sources = [(0, "", {}, direct_zones)]

    for group_index, group_name, group_attributes, group_zones in grouped_zone_sources:
        for zone in group_zones:
            name = zone.attrib.get("name", "").strip()
            if not name:
                name = "<missing territory zone name>"
                issues.append(ValidationIssue("warning", name, "Territory zone is missing the name attribute.", path, "Add a zone name or remove the incomplete territory zone."))
            zones.append(
                TerritoryZone(
                    name=name,
                    attributes={str(key): str(value) for key, value in zone.attrib.items()},
                    element=copy.deepcopy(zone),
                    source_path=path,
                    source_index=source_index,
                    group_index=group_index,
                    group_name=group_name,
                    group_attributes=dict(group_attributes),
                )
            )

    if not zones and root.tag == "territory-type":
        issues.append(ValidationIssue("warning", "", "No <zone> entries found.", path, "Check that you loaded the intended territory XML file."))
    return zones, issues


def parse_territory_groups_file(path: str | os.PathLike[str], source_index: int = 0) -> tuple[list[TerritoryGroup], list[ValidationIssue]]:
    path = str(path)
    try:
        tree = parse_xml_file(path)
    except ET.ParseError as exc:
        return [], [create_xml_parse_issue(path, exc)]
    except OSError as exc:
        return [], [ValidationIssue("error", "", f"Could not read file: {exc}", path, "Check that the file exists, is not locked by another program, and that you have permission to read it.")]

    root = tree.getroot()
    issues: list[ValidationIssue] = []
    if root.tag != "territory-type":
        issues.append(ValidationIssue("error", "", f"Expected root <territory-type>, got <{root.tag}>", path, "This file does not match DayZ territory XML. Load an env/*_territories.xml file."))

    territory_groups = root.findall("territory")
    if not territory_groups and root.findall(".//zone"):
        territory_groups = [root]

    groups: list[TerritoryGroup] = []
    for group_index, group in enumerate(territory_groups):
        attributes = {str(key): str(value) for key, value in group.attrib.items()} if group.tag == "territory" else {}
        groups.append(
            TerritoryGroup(
                source_path=path,
                source_index=source_index,
                group_index=group_index,
                name=attributes.get("name", "").strip(),
                attributes=attributes,
                zone_count=len(group.findall("zone")),
            )
        )
    if not groups and root.tag == "territory-type":
        groups.append(TerritoryGroup(source_path=path, source_index=source_index, group_index=0))
    return groups, issues


def classify_ce_zone_layer(name: str) -> str:
    clean = str(name or "")
    if clean.startswith("valueFlg_"):
        return "value"
    if clean.startswith("usgFlg_Paint-"):
        return "usage_paint"
    if clean.startswith("usgFlg_Def-"):
        return "usage_default"
    if clean.startswith("water-"):
        return "water"
    if clean.startswith("keyPoint-"):
        return "keypoint"
    if clean == "attractors":
        return "attractors"
    return "other"


def safe_int(value: str | int | None, default: int = 0) -> int:
    try:
        return int(str(value or "").strip())
    except (TypeError, ValueError):
        return default


def decode_tga_grayscale(data: bytes) -> tuple[int, int, bytes, bool]:
    if len(data) < 18:
        raise ValueError("TGA file is too small.")
    id_length = data[0]
    color_map_type = data[1]
    image_type = data[2]
    width = int.from_bytes(data[12:14], "little")
    height = int.from_bytes(data[14:16], "little")
    bits_per_pixel = data[16]
    descriptor = data[17]
    if color_map_type != 0:
        raise ValueError("Color-mapped TGA masks are not supported.")
    if width <= 0 or height <= 0:
        raise ValueError("TGA image has invalid dimensions.")
    if bits_per_pixel != 8:
        raise ValueError(f"Expected 8-bit grayscale TGA mask, got {bits_per_pixel}-bit.")
    offset = 18 + id_length
    expected = width * height

    if image_type == 3:
        pixels = data[offset : offset + expected]
        if len(pixels) < expected:
            raise ValueError("Uncompressed TGA pixel data is truncated.")
    elif image_type == 11:
        output = bytearray()
        cursor = offset
        while len(output) < expected:
            if cursor >= len(data):
                raise ValueError("RLE TGA pixel data is truncated.")
            header = data[cursor]
            cursor += 1
            count = (header & 0x7F) + 1
            if header & 0x80:
                if cursor >= len(data):
                    raise ValueError("RLE TGA run packet is truncated.")
                value = data[cursor]
                cursor += 1
                output.extend([value] * count)
            else:
                packet = data[cursor : cursor + count]
                if len(packet) < count:
                    raise ValueError("RLE TGA raw packet is truncated.")
                cursor += count
                output.extend(packet)
        pixels = bytes(output[:expected])
    else:
        raise ValueError(f"Unsupported TGA image type {image_type}; expected grayscale type 3 or 11.")

    top_origin = bool(descriptor & 0x20)
    return width, height, bytes(pixels), top_origin


def encode_tga_grayscale(width: int, height: int, pixels: bytes, top_origin: bool = True) -> bytes:
    expected = int(width) * int(height)
    if width <= 0 or height <= 0:
        raise ValueError("TGA image has invalid dimensions.")
    if len(pixels) != expected:
        raise ValueError(f"Expected {expected} grayscale pixels, got {len(pixels)}.")
    descriptor = 0x20 if top_origin else 0
    header = bytes([
        0,  # ID length
        0,  # no color map
        3,  # uncompressed grayscale
        0, 0, 0, 0, 0,  # color map spec
        0, 0, 0, 0,  # x/y origin
        width & 0xFF,
        (width >> 8) & 0xFF,
        height & 0xFF,
        (height >> 8) & 0xFF,
        8,
        descriptor,
    ])
    return header + bytes(pixels)


def bit_mask_from_plane(plane: bytes, width: int, height: int, bit: int, y_flip: bool = True) -> bytes:
    expected = int(width) * int(height)
    if len(plane) != expected:
        raise ValueError(f"Expected {expected} plane bytes, got {len(plane)}.")
    bit = int(bit)
    if bit <= 0 or bit > 255:
        raise ValueError("Plane bit must be between 1 and 255.")
    table = bytes(255 if value & bit else 0 for value in range(256))
    masked = plane.translate(table)
    if not y_flip:
        return masked
    output = bytearray(expected)
    for y in range(height):
        source_offset = (height - 1 - y) * width
        target_offset = y * width
        output[target_offset : target_offset + width] = masked[source_offset : source_offset + width]
    return bytes(output)


def bit_mask_from_usage_planes(usage_planes: tuple[bytes, bytes, bytes, bytes], width: int, height: int, usage_flags: int, y_flip: bool = True) -> bytes:
    expected = int(width) * int(height)
    if len(usage_planes) != 4 or any(len(plane) != expected for plane in usage_planes):
        raise ValueError(f"Expected four usage planes with {expected} bytes each.")
    usage_flags = int(usage_flags)
    if usage_flags <= 0:
        raise ValueError("Usage flags must be positive.")
    if usage_flags & (usage_flags - 1) == 0:
        bit_index = usage_flags.bit_length() - 1
        plane_index = bit_index // 8
        if plane_index < 4:
            return bit_mask_from_plane(usage_planes[plane_index], width, height, 1 << (bit_index % 8), y_flip=y_flip)
    output = bytearray(expected)
    for y in range(height):
        source_y = height - 1 - y if y_flip else y
        source_offset = source_y * width
        target_offset = y * width
        for x in range(width):
            index = source_offset + x
            value = (
                usage_planes[0][index]
                | (usage_planes[1][index] << 8)
                | (usage_planes[2][index] << 16)
                | (usage_planes[3][index] << 24)
            )
            output[target_offset + x] = 255 if value & usage_flags else 0
    return bytes(output)


def set_bit_from_mask_on_plane(plane: bytes, width: int, height: int, bit: int, mask: bytes, y_flip: bool = True) -> bytes:
    expected = int(width) * int(height)
    if len(plane) != expected or len(mask) != expected:
        raise ValueError(f"Expected {expected} bytes for plane and mask.")
    bit = int(bit)
    if bit <= 0 or bit > 255:
        raise ValueError("Plane bit must be between 1 and 255.")
    clear_mask = 255 ^ bit
    output = bytearray(plane)
    for y in range(height):
        source_y = height - 1 - y if y_flip else y
        source_offset = source_y * width
        mask_offset = y * width
        for x in range(width):
            index = source_offset + x
            if mask[mask_offset + x] > 0:
                output[index] |= bit
            else:
                output[index] &= clear_mask
    return bytes(output)


def set_usage_mask_on_planes(usage_planes: tuple[bytes, bytes, bytes, bytes], width: int, height: int, usage_flags: int, mask: bytes, y_flip: bool = True) -> tuple[bytes, bytes, bytes, bytes]:
    expected = int(width) * int(height)
    if len(usage_planes) != 4 or any(len(plane) != expected for plane in usage_planes):
        raise ValueError(f"Expected four usage planes with {expected} bytes each.")
    usage_flags = int(usage_flags)
    if usage_flags <= 0:
        raise ValueError("Usage flags must be positive.")
    planes = list(usage_planes)
    for bit_index in range(32):
        flag = 1 << bit_index
        if usage_flags & flag:
            plane_index = bit_index // 8
            bit = 1 << (bit_index % 8)
            planes[plane_index] = set_bit_from_mask_on_plane(planes[plane_index], width, height, bit, mask, y_flip=y_flip)
    return (planes[0], planes[1], planes[2], planes[3])


def encode_areaflags_map(area_map: AreaFlagsMap) -> bytes:
    expected = area_map.width * area_map.height
    if len(area_map.usage_planes) != 4 or any(len(plane) != expected for plane in area_map.usage_planes) or len(area_map.value_plane) != expected:
        raise ValueError(f"Expected five areaflags planes with {expected} bytes each.")
    header = struct.pack("<6I", area_map.width, area_map.height, area_map.world_x, area_map.world_z, area_map.cell_size, area_map.reserved)
    return header + b"".join(area_map.usage_planes) + area_map.value_plane


def parse_areaflags_map(path: str | os.PathLike[str]) -> AreaFlagsMap:
    source_path = str(path)
    ensure_not_ignored_storage_path(path)
    data = Path(path).read_bytes()
    if len(data) < 24:
        raise ValueError("areaflags.map is too small to contain a valid header.")
    width, height, world_x, world_z, cell_size, reserved = struct.unpack("<6I", data[:24])
    if width <= 0 or height <= 0:
        raise ValueError("areaflags.map has invalid dimensions.")
    pixel_count = width * height
    expected = 24 + pixel_count * 5
    if len(data) != expected:
        raise ValueError(f"Expected {expected} bytes for {width}x{height} areaflags.map, got {len(data)}.")
    body = data[24:]
    planes = tuple(body[index * pixel_count : (index + 1) * pixel_count] for index in range(5))
    return AreaFlagsMap(
        source_path=source_path,
        width=width,
        height=height,
        world_x=world_x,
        world_z=world_z,
        cell_size=cell_size,
        reserved=reserved,
        usage_planes=(planes[0], planes[1], planes[2], planes[3]),
        value_plane=planes[4],
    )


def parse_ce_zones_project(path: str | os.PathLike[str]) -> CEZoneProject:
    path = str(path)
    issues: list[ValidationIssue] = []
    try:
        tree = parse_xml_file(path)
    except ET.ParseError as exc:
        return CEZoneProject(path, "", 0, 0, [], [], [], [create_xml_parse_issue(path, exc)])
    except OSError as exc:
        return CEZoneProject(path, "", 0, 0, [], [], [], [ValidationIssue("error", "", f"Could not read CE Zones file: {exc}", path, "Check that the CE Tool project XML exists and is readable.")])

    root = tree.getroot()
    if root.tag != "zg-config":
        issues.append(ValidationIssue("error", "", f"Expected root <zg-config>, got <{root.tag}>", path, "Choose a DayZ CE Tool project XML such as chernarusplus.xml."))

    source_dir = Path(path).resolve().parent
    background = root.find("./global/background")
    map_file = background.attrib.get("file", "") if background is not None else ""
    layer_node = root.find("./global/layer")
    world_node = root.find("./global/world")
    layer_size = safe_int(layer_node.attrib.get("size") if layer_node is not None else "", 0)
    world_size = safe_int(world_node.attrib.get("size") if world_node is not None else "", 0)
    if not map_file:
        issues.append(ValidationIssue("warning", "", "CE Tool project has no background map file.", path, "The viewer can still show layers, but map alignment may be limited."))
    if layer_size <= 0:
        issues.append(ValidationIssue("warning", "", "CE Tool project has no valid layer size.", path, "Layer masks need a valid pixel size for proper scaling."))
    if world_size <= 0:
        issues.append(ValidationIssue("warning", "", "CE Tool project has no valid world size.", path, "World coordinates need a valid size for position inspection."))

    usages = [node.attrib.get("name", "").strip() for node in root.findall("./areas/usages/usage")]
    usages = [name for name in usages if name]
    values = [node.attrib.get("name", "").strip() for node in root.findall("./areas/values/value")]
    values = [name for name in values if name]

    layers: list[CEZoneLayer] = []
    for layer in root.findall("./layers/layer"):
        name = layer.attrib.get("name", "").strip()
        if not name:
            issues.append(ValidationIssue("warning", "", "CE Tool layer is missing a name.", path, "Unnamed layers cannot be matched to layer image files."))
            continue
        image_path = str(source_dir / "layers" / f"{name}.tga")
        if not Path(image_path).exists():
            issues.append(ValidationIssue("warning", name, f"Layer image is missing: layers/{name}.tga", path, "Copy the matching .tga layer file beside the CE Tool XML."))
        layers.append(
            CEZoneLayer(
                name=name,
                kind=classify_ce_zone_layer(name),
                usage_flags=safe_int(layer.attrib.get("usage_flags"), 0),
                value_flags=safe_int(layer.attrib.get("value_flags"), 0),
                color=safe_int(layer.attrib.get("color"), 0),
                visible=str(layer.attrib.get("visible", "0")).strip() == "1",
                source_path=path,
                image_path=image_path,
                attributes={str(key): str(value) for key, value in layer.attrib.items()},
            )
        )

    if not layers:
        issues.append(ValidationIssue("warning", "", "No CE zone layers found.", path, "Check that the XML contains a <layers> section."))

    return CEZoneProject(path, map_file, layer_size, world_size, usages, values, layers, issues)


def event_spawn_positions_by_name(spawn_groups: Iterable[EventSpawnGroup]) -> dict[str, list[EventSpawnPosition]]:
    result: dict[str, list[EventSpawnPosition]] = {}
    for group in spawn_groups:
        result.setdefault(group.name.casefold(), []).extend(group.positions)
    return result


def event_secondary_links(events: Iterable[EventEntry]) -> list[EventSecondaryLink]:
    links: list[EventSecondaryLink] = []
    for entry in events:
        secondary_name = entry.child_text("secondary")
        if secondary_name:
            links.append(
                EventSecondaryLink(
                    parent_name=entry.name,
                    secondary_name=secondary_name,
                    parent_source_path=entry.source_path,
                    parent_enabled=entry.is_enabled(),
                )
            )
    return links


def event_secondary_links_by_parent(events: Iterable[EventEntry]) -> dict[str, list[EventSecondaryLink]]:
    result: dict[str, list[EventSecondaryLink]] = {}
    for link in event_secondary_links(events):
        result.setdefault(link.parent_name.casefold(), []).append(link)
    return result


def event_secondary_links_by_secondary(events: Iterable[EventEntry]) -> dict[str, list[EventSecondaryLink]]:
    result: dict[str, list[EventSecondaryLink]] = {}
    for link in event_secondary_links(events):
        result.setdefault(link.secondary_name.casefold(), []).append(link)
    return result


@lru_cache(maxsize=8192)
def _normalize_territory_ref_cached(value: str) -> str:
    return str(value or "").strip().replace("\\", "/").casefold()


def normalize_territory_ref(value: str) -> str:
    return _normalize_territory_ref_cached(str(value or ""))


def normalize_territory_usable(value: str) -> str:
    clean = Path(str(value or "").strip().replace("\\", "/")).stem.casefold()
    if clean.endswith(".xml"):
        clean = clean[:-4]
    return clean


def parse_cfgenvironment_document(path: str | os.PathLike[str]) -> tuple[ET.Element | None, list[ValidationIssue]]:
    path = str(path)
    issues: list[ValidationIssue] = []
    try:
        parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
        tree = parse_xml_file(path, parser=parser)
    except ET.ParseError as exc:
        return None, [create_xml_parse_issue(path, exc)]
    except OSError as exc:
        return None, [ValidationIssue("error", "", f"Could not read file: {exc}", path, "Check that the file exists, is not locked by another program, and that you have permission to read it.")]

    root = tree.getroot()
    if root.tag != "env":
        issues.append(ValidationIssue("error", "", f"Expected root <env>, got <{root.tag}>", path, "This file does not match DayZ cfgenvironment.xml. Load the mission root containing cfgenvironment.xml."))
    return root, issues


def environment_links_from_root(root: ET.Element | None, mission_root: str | os.PathLike[str], source_path: str = "") -> tuple[list[str], dict[str, list[str]], list[ValidationIssue]]:
    root_path = Path(mission_root)
    issues: list[ValidationIssue] = []
    if root is None:
        return [], {}, issues

    territory_root = root.find("territories")
    if territory_root is None:
        issues.append(ValidationIssue("warning", "", "cfgenvironment.xml has no <territories> section.", source_path, "Add a <territories> section before registering environment territory files."))
        return [], {}, issues

    usable_paths: dict[str, str] = {}
    territory_paths: list[str] = []
    for file_element in territory_root.findall("file"):
        file_path = file_element.attrib.get("path", "").strip()
        if not file_path:
            issues.append(ValidationIssue("warning", "", "Environment territory file entry is missing path.", source_path, "Set path to an env/*_territories.xml file or remove the incomplete entry."))
            continue
        resolved = root_path / file_path.replace("\\", os.sep).replace("/", os.sep)
        if is_ignored_storage_path(resolved):
            continue
        usable = normalize_territory_usable(file_path)
        if usable in usable_paths:
            issues.append(ValidationIssue("warning", usable, "Environment territory file is registered more than once.", source_path, "Keep one top-level <file path=\"...\" /> registration for each territory file."))
        usable_paths[usable] = str(resolved)
        territory_paths.append(str(resolved))

    territory_map: dict[str, list[str]] = {}
    for territory in territory_root.findall("territory"):
        territory_name = territory.attrib.get("name", "").strip()
        if not territory_name:
            issues.append(ValidationIssue("warning", "", "Environment territory definition is missing name.", source_path, "Set name so events can resolve this environment population."))
            continue
        mapped_paths: list[str] = []
        for file_element in territory.findall("file"):
            usable = normalize_territory_usable(file_element.attrib.get("usable", ""))
            if not usable:
                issues.append(ValidationIssue("warning", territory_name, "Environment territory file link is missing usable.", source_path, "Set usable to a registered territory filename without path or .xml."))
                continue
            resolved = usable_paths.get(usable, str(root_path / "env" / f"{usable}.xml"))
            if is_ignored_storage_path(resolved):
                continue
            if usable not in usable_paths:
                issues.append(ValidationIssue("warning", territory_name, f"Environment territory uses unregistered file: {usable}.", source_path, "Add a top-level <file path=\"env/..._territories.xml\" /> entry with the same file stem."))
            mapped_paths.append(resolved)
            territory_paths.append(resolved)
        if mapped_paths:
            territory_map[territory_name.casefold()] = dedupe_paths(mapped_paths)

    return dedupe_paths(territory_paths), territory_map, issues


def parse_cfgenvironment_file(path: str | os.PathLike[str], mission_root: str | os.PathLike[str]) -> tuple[list[str], dict[str, list[str]], list[ValidationIssue]]:
    path = str(path)
    root, issues = parse_cfgenvironment_document(path)
    territory_paths, territory_map, link_issues = environment_links_from_root(root, mission_root, path)
    return territory_paths, territory_map, issues + link_issues


def format_cfgenvironment_xml(root: ET.Element) -> str:
    element = copy.deepcopy(root)
    indent_xml(element)
    body = ET.tostring(element, encoding="unicode", short_empty_elements=True)
    return '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>\n' + body + "\n"


def write_cfgenvironment_file(root: ET.Element, output_path: str | os.PathLike[str]) -> int:
    ensure_not_ignored_storage_path(output_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(format_cfgenvironment_xml(root), encoding="utf-8")
    return sum(1 for element in root.iter() if isinstance(element.tag, str))


@lru_cache(maxsize=8192)
def _territory_file_stem_cached(path: str) -> str:
    stem = Path(path).stem.casefold()
    if stem.endswith("_territories"):
        stem = stem[: -len("_territories")]
    return stem.replace("-", "_")


def territory_file_stem(path: str | os.PathLike[str]) -> str:
    return _territory_file_stem_cached(str(path))


def event_territory_hints(event_name: str) -> tuple[str, ...]:
    key = event_name.casefold()
    if key in EVENT_TERRITORY_FILE_HINTS:
        return EVENT_TERRITORY_FILE_HINTS[key]
    for prefix in ("infected", "animal", "ambient"):
        if key.startswith(prefix):
            remainder = key[len(prefix) :]
            if remainder:
                return (remainder.replace("_", ""), remainder)
    return ()


def event_environment_names(event_name: str) -> tuple[str, ...]:
    clean = str(event_name or "").strip()
    values = [clean]
    clean_key = clean.casefold()
    for prefix in ("animal", "ambient", "infected"):
        if clean_key.startswith(prefix):
            suffix = clean[len(prefix) :]
            if suffix:
                values.append(suffix)
            break
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = value.casefold()
        if key and key not in seen:
            deduped.append(value)
            seen.add(key)
    return tuple(deduped)


def territory_zones_for_event(event: EventEntry, zones: Iterable[TerritoryZone], environment_territory_paths: dict[str, list[str]] | None = None) -> list[TerritoryZone]:
    event_key = event.name.casefold()
    environment_path_keys: set[str] = set()
    for name in event_environment_names(event.name):
        for path in (environment_territory_paths or {}).get(name.casefold(), []):
            environment_path_keys.add(normalize_territory_ref(path))
    hints = {hint.casefold().replace("-", "_") for hint in event_territory_hints(event.name)}
    matched: list[TerritoryZone] = []
    for zone in zones:
        zone_key = zone.name.casefold()
        if zone_key == event_key:
            matched.append(zone)
            continue
        if normalize_territory_ref(zone.source_path) in environment_path_keys:
            matched.append(zone)
            continue
        source_stem = territory_file_stem(zone.source_path)
        source_flat = source_stem.replace("_", "")
        if source_stem in hints or source_flat in hints:
            matched.append(zone)
    return matched


def classify_event_name(name: str) -> tuple[str, str, str]:
    clean_name = str(name or "").strip()
    if clean_name == "Loot":
        return "Loot", "Special global loot event", "global"
    for prefix, label, link_target in EVENT_FAMILY_RULES:
        if clean_name.startswith(prefix):
            return prefix, label, link_target
    return "Custom", "Custom or modded event", "unknown"


def validate_event_entries(events: Iterable[EventEntry], check_references: bool = True) -> list[ValidationIssue]:
    event_list = list(events)
    event_names = {event.name.casefold() for event in event_list if not event.name.startswith("<missing ")}
    name_counts = Counter(event.name.casefold() for event in event_list if not event.name.startswith("<missing "))
    issues: list[ValidationIssue] = []

    for event in event_list:
        if event.name.startswith("<missing "):
            continue
        if name_counts[event.name.casefold()] > 1:
            issues.append(ValidationIssue("warning", event.name, "Event name is defined more than once.", event.source_path, "Keep one definition unless separate cfgeconomycore.xml files intentionally override or extend this event."))

        for field_name in EVENT_NUMERIC_FIELDS:
            raw = event.child_text(field_name)
            if not raw:
                continue
            try:
                value = int(raw)
            except ValueError:
                issues.append(ValidationIssue("error", event.name, f"<{field_name}> must be an integer, got {raw!r}.", event.source_path, "Use a non-negative integer."))
                continue
            if value < 0:
                issues.append(ValidationIssue("error", event.name, f"<{field_name}> must be non-negative, got {value}.", event.source_path, "Use zero or a positive integer."))

        position = event.child_text("position")
        if position and position not in EVENT_POSITION_VALUES:
            issues.append(ValidationIssue("warning", event.name, f"Unknown event position mode: {position}.", event.source_path, "Vanilla modes are fixed, player, and uniform. Keep a custom value only when required by a mod."))
        limit = event.child_text("limit")
        if limit and limit not in EVENT_LIMIT_VALUES:
            issues.append(ValidationIssue("warning", event.name, f"Unknown event limit mode: {limit}.", event.source_path, "Vanilla modes are child, parent, mixed, and custom. Keep a custom value only when required by a mod."))
        active = event.child_text("active")
        if active and active not in {"0", "1"}:
            issues.append(ValidationIssue("error", event.name, f"<active> must be 0 or 1, got {active!r}.", event.source_path, "Use 1 to enable the event or 0 to disable it. nominal=0 is not an enable switch."))

        secondary = event.child_text("secondary")
        if check_references and secondary and secondary.casefold() not in event_names:
            issues.append(ValidationIssue("warning", event.name, f"Secondary event is not defined: {secondary}.", event.source_path, "Add an event with that exact name or correct/remove <secondary>."))

        flags = event.element.find("flags")
        if flags is not None:
            for flag_name in EVENT_FLAG_FIELDS:
                raw = flags.attrib.get(flag_name, "").strip()
                if not raw:
                    issues.append(ValidationIssue("warning", event.name, f"<flags> is missing {flag_name}.", event.source_path, f"Add {flag_name}=\"0\" or {flag_name}=\"1\"."))
                elif raw not in {"0", "1"}:
                    issues.append(ValidationIssue("error", event.name, f"<flags {flag_name}> must be 0 or 1, got {raw!r}.", event.source_path, f"Change {flag_name} to 0 or 1."))

        for child in event.element.findall("./children/child"):
            child_type = child.attrib.get("type", "").strip()
            if not child_type:
                issues.append(ValidationIssue("error", event.name, "Event child is missing its type classname.", event.source_path, "Set child type to the entity classname spawned by this event."))
            for field_name in ("min", "max", "lootmin", "lootmax"):
                raw = child.attrib.get(field_name, "").strip()
                if not raw:
                    continue
                try:
                    value = int(raw)
                except ValueError:
                    issues.append(ValidationIssue("error", event.name, f"Child {child_type or '<missing type>'} {field_name} must be an integer, got {raw!r}.", event.source_path, "Use a non-negative integer."))
                    continue
                if value < 0:
                    issues.append(ValidationIssue("error", event.name, f"Child {child_type or '<missing type>'} {field_name} must be non-negative, got {value}.", event.source_path, "Use zero or a positive integer."))

    return issues


def validate_event_spawn_links(
    events: Iterable[EventEntry],
    spawn_groups: Iterable[EventSpawnGroup],
    territory_zones: Iterable[TerritoryZone] | None = None,
    environment_territory_paths: dict[str, list[str]] | None = None,
    event_groups: Iterable[EventGroupDefinition] = (),
) -> list[ValidationIssue]:
    event_list = list(events)
    spawn_group_list = list(spawn_groups)
    event_group_list = list(event_groups)
    zone_list = list(territory_zones) if territory_zones is not None else None
    environment_map = environment_territory_paths or {}
    event_names = {event.name.casefold(): event for event in event_list}
    positions_by_name = event_spawn_positions_by_name(spawn_group_list)
    spawn_names = {group.name.casefold() for group in spawn_group_list if not group.name.startswith("<missing ")}
    event_group_names = {group.name.casefold() for group in event_group_list if not group.name.startswith("<missing ")}
    event_group_name_counts = Counter(group.name.casefold() for group in event_group_list if not group.name.startswith("<missing "))
    referenced_group_names: set[str] = set()
    secondary_links_by_secondary = event_secondary_links_by_secondary(event_list)
    issues: list[ValidationIssue] = []

    for event_group in event_group_list:
        if not event_group.name.startswith("<missing ") and event_group_name_counts[event_group.name.casefold()] > 1:
            issues.append(ValidationIssue("error", event_group.name, "Event group name is defined more than once across loaded sources.", event_group.source_path, "Keep one effective group definition so position references are unambiguous."))

    for event in event_list:
        if event.name.startswith("<missing "):
            continue
        if not event.is_enabled():
            continue
        family, label, link_target = classify_event_name(event.name)
        position = event.child_text("position")
        if link_target == "unknown" and position == "fixed":
            label = "Fixed-position event"
            link_target = "cfgeventspawns"
        requires_fixed_positions = link_target == "cfgeventspawns" and position not in {"player", "uniform"}
        if requires_fixed_positions and event.name.casefold() not in spawn_names:
            issues.append(
                ValidationIssue(
                    "info",
                    event.name,
                    "No cfgeventspawns.xml positions found in loaded physical files for this event.",
                    event.source_path,
                    f"{label} may use terrain-default CE data or another positioning system. Add a mission override only when needed.",
                )
            )
        elif requires_fixed_positions:
            positions = positions_by_name.get(event.name.casefold(), [])
            nominal = parse_int(event.child_text("nominal"))
            if nominal is not None and nominal > 0 and positions and nominal > len(positions):
                issues.append(ValidationIssue("info", event.name, f"Nominal target {nominal} exceeds {len(positions)} loaded explicit candidate position(s).", event.source_path, "Actual behavior may be constrained by event logic, terrain defaults, and persisted CE state."))
        if link_target == "environment" and zone_list is not None:
            zones = territory_zones_for_event(event, zone_list, environment_map)
            if not zones:
                parent_links = [
                    link for link in secondary_links_by_secondary.get(event.name.casefold(), [])
                    if link.parent_enabled
                ]
                if family == "Infected" and parent_links:
                    continue
                severity = "info" if family == "Infected" else "warning"
                suggestion = (
                    "This can be valid for Infected events referenced through <secondary>, such as static situations. Verify the parent event or custom setup if the infected should still appear."
                    if family == "Infected"
                    else "Check cfgenvironment.xml and env territory files. Animal and Ambient events usually map by territory name."
                )
                issues.append(
                    ValidationIssue(
                        severity,
                        event.name,
                        "No territory zones found for this enabled environment event.",
                        event.source_path,
                        suggestion,
                    )
                )
            if family in {"Animal", "Ambient"}:
                mapped_paths: list[str] = []
                for name in event_environment_names(event.name):
                    mapped_paths.extend(environment_map.get(name.casefold(), []))
                if not mapped_paths:
                    issues.append(
                        ValidationIssue(
                            "warning",
                            event.name,
                            "No cfgenvironment.xml territory mapping found for this enabled event.",
                            event.source_path,
                            "Add or fix the matching <territory name=\"...\"> entry in cfgenvironment.xml. For example, AnimalDeer should map through name=\"Deer\"; AmbientHen may map through name=\"AmbientHen\".",
                        )
                    )

    for spawn_group in spawn_group_list:
        if spawn_group.name.startswith("<missing "):
            continue
        if spawn_group.name.casefold() not in event_names:
            issues.append(
                ValidationIssue(
                    "warning",
                    spawn_group.name,
                    "cfgeventspawns.xml references an event that is not defined in events.xml.",
                    spawn_group.source_path,
                    "This may come from terrain-default or mod event data. Add/fix the event only when the effective event set truly lacks it.",
                )
            )
        for position in spawn_group.positions:
            group_name = position.attributes.get("group", "").strip()
            if not group_name:
                continue
            group_key = group_name.casefold()
            referenced_group_names.add(group_key)
            if group_key not in event_group_names:
                issues.append(
                    ValidationIssue(
                        "error",
                        spawn_group.name,
                        f"Spawn position references missing event group: {group_name}.",
                        position.source_path,
                        "Add the matching <group name=\"...\"> to cfgeventgroups.xml or correct/remove the group reference.",
                    )
                )

    for event_group in event_group_list:
        if event_group.name.startswith("<missing "):
            continue
        if event_group.name.casefold() not in referenced_group_names:
            issues.append(
                ValidationIssue(
                    "info",
                    event_group.name,
                    "Event group is not referenced by any loaded cfgeventspawns.xml position.",
                    event_group.source_path,
                    "Unused groups can be intentional seasonal, disabled, future, or mod-compatibility content.",
                )
            )

    return issues


def parse_type_entry_xml(raw_xml: str, source_path: str = "", source_index: int = 0) -> TypeEntry:
    element = ET.fromstring(raw_xml.strip())
    if element.tag != "type":
        raise ValueError(f"Expected root <type>, got <{element.tag}>.")
    name = element.attrib.get("name", "").strip()
    if not name:
        raise ValueError("Type entry is missing the name attribute.")
    order_type_entry_children(element)
    return TypeEntry(name=name, element=copy.deepcopy(element), source_path=source_path, source_index=source_index)


def parse_cfglimitsdefinition_file(path: str | os.PathLike[str]) -> tuple[dict[str, list[str]], list[ValidationIssue]]:
    path = str(path)
    definitions = {field: set() for field in RELATION_FIELDS}
    try:
        root = parse_xml_file(path).getroot()
    except ET.ParseError as exc:
        return {field: [] for field in RELATION_FIELDS}, [create_xml_parse_issue(path, exc)]
    except OSError as exc:
        return {field: [] for field in RELATION_FIELDS}, [ValidationIssue("error", "", f"Could not read file: {exc}", path, "Check that the file exists, is not locked by another program, and that you have permission to read it.")]

    for element in root.iter():
        tag = element.tag.casefold()
        if tag in definitions:
            name = element.attrib.get("name", "").strip()
            if name:
                definitions[tag].add(name)

    return {field: sorted(values, key=str.casefold) for field, values in definitions.items()}, []


def write_cfglimitsdefinition_file(definitions: dict[str, list[str]], output_path: str | os.PathLike[str]) -> None:
    ensure_not_ignored_storage_path(output_path)
    root = ET.Element("lists")
    for field in RELATION_FIELDS:
        group_tag, child_tag = RELATION_DEFINITION_GROUPS[field]
        group = ET.SubElement(root, group_tag)
        seen: set[str] = set()
        for value in definitions.get(field, []):
            name = str(value).strip()
            key = name.casefold()
            if not name or key in seen:
                continue
            ET.SubElement(group, child_tag, {"name": name})
            seen.add(key)

    indent_xml(root)
    tree = ET.ElementTree(root)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    tree.write(output_path, encoding="utf-8", xml_declaration=True)


def parse_cfgeconomycore_file_refs(path: str | os.PathLike[str], mission_root: str | os.PathLike[str], file_type: str, display_name: str | None = None) -> tuple[list[str], list[ValidationIssue]]:
    path = str(path)
    root_path = Path(mission_root)
    wanted_type = str(file_type or "").strip().casefold()
    label = display_name or wanted_type
    matched_paths: list[str] = []
    issues: list[ValidationIssue] = []
    try:
        root = parse_xml_file(path).getroot()
    except ET.ParseError as exc:
        return [], [create_xml_parse_issue(path, exc)]
    except OSError as exc:
        return [], [ValidationIssue("error", "", f"Could not read file: {exc}", path, "Check that the file exists, is not locked by another program, and that you have permission to read it.")]

    for ce_element in root.findall(".//ce"):
        folder = ce_element.attrib.get("folder", "").strip()
        if not folder:
            issues.append(ValidationIssue("warning", "", "<ce> entry is missing the folder attribute.", path, "Add folder=\"FolderName\" or remove the empty CE block."))
            continue
        ce_folder = root_path / folder
        for file_element in ce_element.findall("file"):
            current_type = file_element.attrib.get("type", "").strip().casefold()
            if current_type != wanted_type:
                continue
            filename = file_element.attrib.get("name", "").strip()
            if not filename:
                issues.append(ValidationIssue("warning", "", f"<file type=\"{wanted_type}\"> entry is missing the name attribute.", path, f"Add name=\"your_{wanted_type}_file.xml\" or remove the broken entry."))
                continue
            resolved_path = ce_folder / filename
            if is_ignored_storage_path(resolved_path):
                continue
            if resolved_path.is_file():
                matched_paths.append(str(resolved_path))
            else:
                issues.append(ValidationIssue("warning", "", f"cfgeconomycore.xml references missing {label} file: {resolved_path}", path, "Create the referenced file, fix the CE folder/name, or remove that active file entry."))

    return dedupe_paths(matched_paths), issues


def parse_cfgeconomycore_types(path: str | os.PathLike[str], mission_root: str | os.PathLike[str]) -> tuple[list[str], list[ValidationIssue]]:
    return parse_cfgeconomycore_file_refs(path, mission_root, "types", "types")


def parse_cfgeconomycore_spawnabletypes(path: str | os.PathLike[str], mission_root: str | os.PathLike[str]) -> tuple[list[str], list[ValidationIssue]]:
    return parse_cfgeconomycore_file_refs(path, mission_root, "spawnabletypes", "spawnable types")


def parse_cfgeconomycore_randompresets(path: str | os.PathLike[str], mission_root: str | os.PathLike[str]) -> tuple[list[str], list[ValidationIssue]]:
    return parse_cfgeconomycore_file_refs(path, mission_root, "randompresets", "random presets")


def weather_default_values() -> dict[str, str]:
    return dict(WEATHER_DEFAULT_VALUES)


def weather_preset_values(name: str) -> dict[str, str]:
    values = weather_default_values()
    values.update(WEATHER_PRESETS.get(name, {}))
    return values




def validate_weather_values(values: dict[str, str], source_path: str = "") -> list[ValidationIssue]:
    data = weather_default_values()
    data.update({str(key): "" if value is None else str(value).strip() for key, value in values.items()})
    issues: list[ValidationIssue] = []
    numbers: dict[str, float] = {}

    def add(severity: str, key: str, message: str, suggestion: str) -> None:
        issues.append(ValidationIssue(severity, key, message, source_path, suggestion))

    for flag_key in ("reset", "enable"):
        raw = data.get(flag_key, "")
        if raw.casefold() not in {"0", "1", "true", "false", "yes", "no"}:
            add("error", flag_key, f"Weather {flag_key} must be a boolean value, got {raw or 'empty'}.", "Use 0/1, true/false, or yes/no.")

    for key in WEATHER_DEFAULT_VALUES:
        if key in {"reset", "enable"}:
            continue
        raw = data.get(key, "")
        try:
            number = float(raw)
        except (TypeError, ValueError):
            add("error", key, f"Weather value is not numeric: {key}={raw or 'empty'}.", "Enter a numeric value. Use dots for decimals, for example 0.75.")
            continue
        if number == float("inf") or number == float("-inf") or number != number:
            add("error", key, f"Weather value is not finite: {key}={raw}.", "Use a normal finite number.")
            continue
        numbers[key] = number

    def value(key: str) -> float | None:
        return numbers.get(key)

    def check_01(key: str, label: str) -> None:
        number = value(key)
        if number is not None and not 0.0 <= number <= 1.0:
            add("error", key, f"{label} must be between 0 and 1, got {data[key]}.", "Use a value from 0.0 to 1.0.")

    def check_non_negative(key: str, label: str) -> None:
        number = value(key)
        if number is not None and number < 0:
            add("error", key, f"{label} must not be negative, got {data[key]}.", "Use 0 or a positive value.")

    def check_pair(min_key: str, max_key: str, label: str) -> None:
        min_value = value(min_key)
        max_value = value(max_key)
        if min_value is not None and max_value is not None and min_value > max_value:
            add("error", min_key, f"{label} min is higher than max: {data[min_key]} > {data[max_key]}.", "Lower the min value or raise the max value.")

    normalized_sections = ("overcast", "fog", "rain", "snowfall")
    weather_sections = normalized_sections + ("windMagnitude", "windDirection")
    for section in weather_sections:
        check_pair(f"{section}.limits.min", f"{section}.limits.max", section)
        check_pair(f"{section}.timelimits.min", f"{section}.timelimits.max", f"{section} time limits")
        check_pair(f"{section}.changelimits.min", f"{section}.changelimits.max", f"{section} change limits")
        for time_key in (f"{section}.current.time", f"{section}.current.duration", f"{section}.timelimits.min", f"{section}.timelimits.max"):
            check_non_negative(time_key, time_key)
        actual = value(f"{section}.current.actual")
        min_limit = value(f"{section}.limits.min")
        max_limit = value(f"{section}.limits.max")
        if actual is not None and min_limit is not None and max_limit is not None and not min_limit <= actual <= max_limit:
            add("warning", f"{section}.current.actual", f"{section} current value is outside its random limits: {data[f'{section}.current.actual']} not in {data[f'{section}.limits.min']}..{data[f'{section}.limits.max']}.", "Keep the initial current value inside the configured limits unless this is intentional.")

    for section in normalized_sections:
        for suffix in ("current.actual", "limits.min", "limits.max", "changelimits.min", "changelimits.max"):
            check_01(f"{section}.{suffix}", f"{section}.{suffix}")

    for key in ("windMagnitude.current.actual", "windMagnitude.limits.min", "windMagnitude.limits.max", "windMagnitude.changelimits.min", "windMagnitude.changelimits.max"):
        check_non_negative(key, key)

    for section in ("rain", "snowfall"):
        check_pair(f"{section}.thresholds.min", f"{section}.thresholds.max", f"{section} overcast threshold")
        for suffix in ("thresholds.min", "thresholds.max"):
            check_01(f"{section}.{suffix}", f"{section}.{suffix}")
        check_non_negative(f"{section}.thresholds.end", f"{section}.thresholds.end")
        precipitation_max = value(f"{section}.limits.max")
        threshold_min = value(f"{section}.thresholds.min")
        overcast_max = value("overcast.limits.max")
        if precipitation_max is not None and precipitation_max > 0 and threshold_min is not None and overcast_max is not None and threshold_min > overcast_max:
            add("warning", f"{section}.thresholds.min", f"{section} can never start: threshold min {data[f'{section}.thresholds.min']} is above overcast max {data['overcast.limits.max']}.", "Lower the precipitation threshold or raise overcast.limits.max.")

    for key, label in (("storm.density", "Lightning density"), ("storm.threshold", "Lightning threshold")):
        check_01(key, label)
    check_non_negative("storm.timeout", "Lightning timeout")
    storm_threshold = value("storm.threshold")
    overcast_max = value("overcast.limits.max")
    density = value("storm.density")
    if density is not None and density > 0 and storm_threshold is not None and overcast_max is not None and storm_threshold > overcast_max:
        add("warning", "storm.threshold", f"Lightning can never start: storm threshold {data['storm.threshold']} is above overcast max {data['overcast.limits.max']}.", "Lower storm.threshold or raise overcast.limits.max.")

    return issues

def parse_weather_config_file(path: str | os.PathLike[str]) -> tuple[dict[str, str], list[ValidationIssue]]:
    source = Path(path)
    values = weather_default_values()
    issues: list[ValidationIssue] = []
    try:
        root = parse_xml_file(source).getroot()
    except ET.ParseError as exc:
        return values, [create_xml_parse_issue(str(source), exc)]
    except OSError as exc:
        return values, [ValidationIssue("error", "", f"Could not read cfgweather.xml: {exc}", str(source), "Check file permissions.")]
    if root.tag != "weather":
        issues.append(ValidationIssue("error", "", f"Expected root <weather>, got <{root.tag}>", str(source), "Load a DayZ cfgweather.xml file."))
        return values, issues
    values["reset"] = root.attrib.get("reset", values["reset"])
    values["enable"] = root.attrib.get("enable", values["enable"])

    for section in ("overcast", "fog", "rain", "windMagnitude", "windDirection", "snowfall"):
        section_node = root.find(section)
        if section_node is None:
            issues.append(ValidationIssue("warning", section, f"Missing <{section}> weather section.", str(source), f"Add <{section}> or apply a weather preset."))
            continue
        for child_name in ("current", "limits", "timelimits", "changelimits", "thresholds"):
            child = section_node.find(child_name)
            if child is None:
                continue
            for attr, attr_value in child.attrib.items():
                key = f"{section}.{child_name}.{attr}"
                if key in values:
                    values[key] = str(attr_value)

    # Legacy pre-1.29-style wind block fallback. Current DayZ config uses windMagnitude + windDirection.
    wind = root.find("wind")
    if wind is not None:
        maxspeed = wind.find("maxspeed")
        if maxspeed is not None and maxspeed.text is not None:
            values["windMagnitude.limits.max"] = maxspeed.text.strip()
        params = wind.find("params")
        if params is not None:
            if "min" in params.attrib:
                values["windMagnitude.limits.min"] = str(params.attrib["min"])
            if "max" in params.attrib:
                values["windMagnitude.limits.max"] = str(params.attrib["max"])
            if "frequency" in params.attrib:
                values["windMagnitude.timelimits.min"] = str(params.attrib["frequency"])

    storm = root.find("storm")
    if storm is not None:
        for attr, attr_value in storm.attrib.items():
            key = f"storm.{attr}"
            if key in values:
                values[key] = str(attr_value)
    else:
        issues.append(ValidationIssue("warning", "storm", "Missing <storm> weather section.", str(source), "Add <storm> or apply a weather preset."))
    issues.extend(validate_weather_values(values, str(source)))
    return values, issues


def weather_config_to_element(values: dict[str, str]) -> ET.Element:
    data = weather_default_values()
    data.update({str(key): str(value) for key, value in values.items() if value is not None})
    root = ET.Element("weather", {"reset": data["reset"], "enable": data["enable"]})
    for section in ("overcast", "fog", "rain", "windMagnitude", "windDirection", "snowfall"):
        section_node = ET.SubElement(root, section)
        ET.SubElement(section_node, "current", {key: data[f"{section}.current.{key}"] for key in ("actual", "time", "duration")})
        ET.SubElement(section_node, "limits", {key: data[f"{section}.limits.{key}"] for key in ("min", "max")})
        ET.SubElement(section_node, "timelimits", {key: data[f"{section}.timelimits.{key}"] for key in ("min", "max")})
        ET.SubElement(section_node, "changelimits", {key: data[f"{section}.changelimits.{key}"] for key in ("min", "max")})
        if section in {"rain", "snowfall"}:
            ET.SubElement(section_node, "thresholds", {key: data[f"{section}.thresholds.{key}"] for key in ("min", "max", "end")})
    ET.SubElement(root, "storm", {key: data[f"storm.{key}"] for key in ("density", "threshold", "timeout")})
    return root


def format_weather_config_xml(values: dict[str, str]) -> str:
    root = weather_config_to_element(values)
    indent_xml(root)
    return "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n" + ET.tostring(root, encoding="unicode") + "\n"


def write_weather_config_file(values: dict[str, str], output_path: str | os.PathLike[str]) -> None:
    ensure_not_ignored_storage_path(output_path)
    Path(output_path).write_text(format_weather_config_xml(values), encoding="utf-8")


def strip_config_comments(content: str) -> str:
    if not content:
        return ""

    result = []
    index = 0
    in_string = ""
    escaped = False

    while index < len(content):
        char = content[index]
        next_char = content[index + 1] if index + 1 < len(content) else ""

        if in_string:
            result.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == in_string:
                in_string = ""
            index += 1
            continue

        if char in {'"', "'"}:
            in_string = char
            result.append(char)
            index += 1
            continue

        if char == "/" and next_char == "/":
            index += 2
            while index < len(content) and content[index] not in "\r\n":
                index += 1
            continue

        if char == "/" and next_char == "*":
            index += 2
            while index < len(content):
                if content[index] == "*" and index + 1 < len(content) and content[index + 1] == "/":
                    index += 2
                    break
                index += 1
            continue

        result.append(char)
        index += 1

    return "".join(result)


def find_matching_config_brace(content: str, open_index: int) -> int:
    depth = 0
    in_string = ""
    escaped = False

    for index in range(open_index, len(content)):
        char = content[index]

        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == in_string:
                in_string = ""
            continue

        if char in {'"', "'"}:
            in_string = char
            continue

        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index

    return -1


def iter_direct_config_class_blocks(content: str):
    pattern = re.compile(r"\bclass\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?::\s*([A-Za-z_][A-Za-z0-9_]*))?\s*\{", re.IGNORECASE)
    position = 0

    while True:
        match = pattern.search(content, position)
        if not match:
            break

        open_index = content.find("{", match.start())
        close_index = find_matching_config_brace(content, open_index)
        if close_index < 0:
            position = match.end()
            continue

        yield match.group(1), match.group(2) or "", content[open_index + 1 : close_index]
        position = close_index + 1


def strip_nested_config_class_blocks(content: str) -> str:
    if not content:
        return ""
    chars = list(content)
    pattern = re.compile(r"\bclass\s+[A-Za-z_][A-Za-z0-9_]*\s*(?::\s*[A-Za-z_][A-Za-z0-9_]*)?\s*\{", re.IGNORECASE)
    position = 0
    while True:
        match = pattern.search(content, position)
        if not match:
            break
        open_index = content.find("{", match.start())
        close_index = find_matching_config_brace(content, open_index)
        if close_index < 0:
            position = match.end()
            continue
        end = close_index + 1
        while end < len(content) and content[end].isspace():
            end += 1
        if end < len(content) and content[end] == ";":
            end += 1
        for index in range(match.start(), end):
            chars[index] = " "
        position = end
    return "".join(chars)


def parse_config_scope(content: str) -> str:
    direct_content = strip_nested_config_class_blocks(content)
    match = re.search(r"\bscope\s*=\s*([0-9]+)\s*;", direct_content, re.IGNORECASE)
    return match.group(1) if match else ""


def parse_config_class_entries(content: str, source_path: str = "") -> list[ConfigClassEntry]:
    clean = strip_config_comments(content)
    entries: list[ConfigClassEntry] = []

    for section, _base, body in iter_direct_config_class_blocks(clean):
        section_name = next((known for known in CONFIG_TYPE_SECTIONS if known.casefold() == section.casefold()), "")
        if not section_name:
            continue

        for class_name, class_base, class_body in iter_direct_config_class_blocks(body):
            entries.append(ConfigClassEntry(class_name, section_name, source_path, class_base, class_body, parse_config_scope(class_body)))

    return entries


def decode_config_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def read_config_text_file(path: str | os.PathLike[str]) -> str:
    ensure_not_ignored_storage_path(path)
    return decode_config_text(Path(path).read_bytes())


def find_tool(possible: Iterable[str | os.PathLike[str]]) -> str:
    for path in possible:
        if Path(path).is_file():
            return str(path)
    return ""


def find_cfgconvert() -> str:
    pf86 = os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")
    pf = os.environ.get("ProgramFiles", "C:/Program Files")
    return find_tool(
        [
            Path(pf86) / "Steam/steamapps/common/DayZ Tools/Bin/CfgConvert/CfgConvert.exe",
            Path(pf) / "Steam/steamapps/common/DayZ Tools/Bin/CfgConvert/CfgConvert.exe",
        ]
    )


def get_hidden_subprocess_kwargs() -> dict:
    kwargs = {"stdout": subprocess.PIPE, "stderr": subprocess.STDOUT}
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0
        kwargs["startupinfo"] = startupinfo
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return kwargs


def convert_config_bin_data_to_text(data: bytes, cfgconvert_exe: str, source_label: str = "config.bin") -> str:
    if not cfgconvert_exe or not Path(cfgconvert_exe).is_file():
        raise RuntimeError("CfgConvert.exe not found.")

    with tempfile.TemporaryDirectory(prefix="rag-economy-cfgconvert-") as temp_dir:
        temp_path = Path(temp_dir)
        bin_path = temp_path / "config.bin"
        cpp_path = temp_path / "config.cpp"
        bin_path.write_bytes(data)
        command = [cfgconvert_exe, "-txt", "-dst", str(cpp_path), str(bin_path)]
        completed = subprocess.run(command, cwd=str(temp_path), text=True, **get_hidden_subprocess_kwargs())
        if completed.returncode != 0 or not cpp_path.is_file():
            output = (completed.stdout or "").strip()
            detail = f": {output}" if output else ""
            raise RuntimeError(f"CfgConvert failed for {source_label} with exit code {completed.returncode}{detail}")
        return read_config_text_file(cpp_path)


def convert_config_bin_file_to_text(path: str | os.PathLike[str], cfgconvert_exe: str) -> str:
    ensure_not_ignored_storage_path(path)
    return convert_config_bin_data_to_text(Path(path).read_bytes(), cfgconvert_exe, str(path))


def config_source_files(path: str | os.PathLike[str]) -> list[str]:
    root = Path(path)
    if is_ignored_storage_path(root):
        return []
    if root.is_file():
        return [str(root)] if root.name.lower() in {"config.cpp", "config.hpp", "config.bin"} or root.suffix.lower() in {".cpp", ".hpp"} else []
    if not root.is_dir():
        return []
    return [
        str(item)
        for item in sorted(iter_files_ignoring_storage(root), key=lambda candidate: str(candidate).casefold())
        if item.is_file() and (item.name.lower() in {"config.cpp", "config.hpp", "config.bin"} or item.suffix.lower() in {".cpp", ".hpp"})
    ]


def extract_config_class_entries(path: str | os.PathLike[str], cfgconvert_exe: str = "") -> tuple[list[ConfigClassEntry], list[ValidationIssue]]:
    return extract_config_class_entries_internal(path, cfgconvert_exe=cfgconvert_exe, public_only=True)


def extract_config_class_entries_internal(path: str | os.PathLike[str], cfgconvert_exe: str = "", public_only: bool = True) -> tuple[list[ConfigClassEntry], list[ValidationIssue]]:
    source = Path(path)
    issues: list[ValidationIssue] = []
    entries: list[ConfigClassEntry] = []

    if not source.exists():
        return [], [ValidationIssue("error", "", f"Path does not exist: {source}", str(source), "Choose a config.cpp, config.hpp, folder, or PBO file.")]

    if source.is_file() and source.suffix.lower() == ".pbo":
        if read_pbo_archive is None or read_pbo_entry_data is None:
            return [], [ValidationIssue("error", "", "PBO reader is not available in this build.", str(source), "Bundle pbo_core.py with the application.")]
        try:
            archive = read_pbo_archive(str(source))
        except PboError as exc:
            return [], [ValidationIssue("error", "", f"Could not read PBO: {exc}", str(source), "Check that the file is a valid unencrypted PBO.")]

        config_entries = [entry.name for entry in archive["entries"] if entry.name.replace("/", "\\").lower().endswith(("config.cpp", "config.hpp"))]
        config_bins = [entry.name for entry in archive["entries"] if entry.name.replace("/", "\\").lower().endswith("config.bin")]
        if config_bins and not cfgconvert_exe:
            issues.append(ValidationIssue("warning", "", "PBO contains config.bin but CfgConvert.exe is not configured.", str(source), "Select DayZ Tools CfgConvert.exe in the generator to read binarized configs."))
        if not config_entries and not config_bins:
            issues.append(ValidationIssue("warning", "", "No config.cpp, config.hpp, or config.bin found in PBO.", str(source), "Choose a PBO or source folder that contains addon config data."))
            return entries, issues

        for entry_name in config_entries:
            try:
                data = read_pbo_entry_data(str(source), entry_name, max_bytes=8 * 1024 * 1024)
            except PboError as exc:
                issues.append(ValidationIssue("warning", "", f"Could not read PBO config entry {entry_name}: {exc}", str(source), "Encrypted or unsupported PBO entries cannot be parsed."))
                continue
            entries.extend(parse_config_class_entries(decode_config_text(data), f"{source}!{entry_name}"))

        if cfgconvert_exe:
            for entry_name in config_bins:
                try:
                    data = read_pbo_entry_data(str(source), entry_name, max_bytes=8 * 1024 * 1024)
                    text = convert_config_bin_data_to_text(data, cfgconvert_exe, f"{source}!{entry_name}")
                except (PboError, RuntimeError) as exc:
                    issues.append(ValidationIssue("warning", "", f"Could not convert PBO config entry {entry_name}: {exc}", str(source), "Check CfgConvert.exe and verify the PBO entry is a real rapified config."))
                    continue
                entries.extend(parse_config_class_entries(text, f"{source}!{entry_name}"))
        entries = dedupe_config_class_entries(entries)
        return (public_config_class_entries(entries) if public_only else entries), issues

    config_files = config_source_files(source)
    if not config_files:
        return [], [ValidationIssue("warning", "", "No config.cpp, config.hpp, or config.bin files found.", str(source), "Choose a config.cpp, config.hpp, config.bin, source folder, or PBO file.")]

    for config_path in config_files:
        try:
            if Path(config_path).name.lower() == "config.bin":
                if not cfgconvert_exe:
                    issues.append(ValidationIssue("warning", "", "config.bin found but CfgConvert.exe is not configured.", config_path, "Select DayZ Tools CfgConvert.exe in the generator to read binarized configs."))
                    continue
                text = convert_config_bin_file_to_text(config_path, cfgconvert_exe)
            else:
                text = read_config_text_file(config_path)
            entries.extend(parse_config_class_entries(text, config_path))
        except (OSError, RuntimeError) as exc:
            issues.append(ValidationIssue("warning", "", f"Could not read config file: {exc}", config_path, "Check file permissions and try again."))

    entries = dedupe_config_class_entries(entries)
    return (public_config_class_entries(entries) if public_only else entries), issues


def extract_config_class_entries_from_sources(paths: Iterable[str | os.PathLike[str]], cfgconvert_exe: str = "") -> tuple[list[ConfigClassEntry], list[ValidationIssue]]:
    entries: list[ConfigClassEntry] = []
    issues: list[ValidationIssue] = []
    for path in paths:
        path_entries, path_issues = extract_config_class_entries_internal(path, cfgconvert_exe=cfgconvert_exe, public_only=False)
        entries.extend(path_entries)
        issues.extend(path_issues)
    return public_config_class_entries(dedupe_config_class_entries(entries)), issues


def dedupe_config_class_entries(entries: Iterable[ConfigClassEntry]) -> list[ConfigClassEntry]:
    result: dict[tuple[str, str], ConfigClassEntry] = {}
    order: list[tuple[str, str]] = []
    for entry in entries:
        key = (entry.section.casefold(), entry.name.casefold())
        if key not in result:
            order.append(key)
            result[key] = entry
    return sorted((result[key] for key in order), key=lambda item: (item.section.casefold(), item.name.casefold()))


def is_public_config_class(entry: ConfigClassEntry) -> bool:
    return str(entry.scope).strip() == "2"


def public_config_class_entries(entries: Iterable[ConfigClassEntry]) -> list[ConfigClassEntry]:
    config_entries = dedupe_config_class_entries(entries)
    class_lookup = build_config_class_lookup(config_entries)
    category_cache: dict[str, str] = {}
    public_entries = []
    for entry in config_entries:
        if not is_public_config_class(entry):
            continue
        category = infer_config_type_category(entry, class_lookup, category_cache=category_cache)
        public_entries.append(ConfigClassEntry(entry.name, entry.section, entry.source_path, entry.base_class, entry.body, entry.scope, category))
    return public_entries


def classname_has_any(name: str, keywords: Iterable[str]) -> bool:
    clean = re.sub(r"[^a-z0-9]+", "_", name.casefold())
    return any(keyword in clean for keyword in keywords)


def parse_config_array_values(content: str, array_name: str) -> list[str]:
    direct_content = strip_nested_config_class_blocks(content or "")
    pattern = re.compile(r"\b" + re.escape(array_name) + r"\s*\[\s*\]\s*\+?=\s*\{(.*?)\}\s*;", re.IGNORECASE | re.DOTALL)
    match = pattern.search(direct_content)
    if not match:
        return []
    values = []
    for item in match.group(1).split(","):
        value = item.strip().strip('"').strip("'")
        if value:
            values.append(value)
    return values


def infer_category_from_keywords(text: str, mappings: Iterable[tuple[str, Iterable[str]]]) -> str:
    for category, keywords in mappings:
        if classname_has_any(text, keywords):
            return category
    return ""


def build_config_class_lookup(entries: Iterable[ConfigClassEntry]) -> dict[str, ConfigClassEntry]:
    lookup: dict[str, ConfigClassEntry] = {}
    for entry in entries:
        lookup.setdefault(entry.name.casefold(), entry)
    return lookup


def infer_config_type_category(entry: ConfigClassEntry, class_lookup: dict[str, ConfigClassEntry] | None = None, visited: set[str] | None = None, category_cache: dict[str, str] | None = None) -> str:
    name = entry.name
    section = entry.section.casefold()
    visited = visited or set()
    entry_key = entry.name.casefold()
    if category_cache is not None and entry_key in category_cache:
        return category_cache[entry_key]
    if entry_key in visited:
        return ""
    visited = visited | {entry_key}

    def finish(category: str) -> str:
        if category_cache is not None:
            category_cache[entry_key] = category
        return category

    explosive_keywords = (
        "grenade",
        "landmine",
        "_mine",
        "claymore",
        "explosive",
        "plastic_explosive",
        "ied",
        "tripwire",
        "trap",
        "satchel",
        "bomb",
        "charge",
        "detonator",
    )

    vehicle_category_keywords = [
        ("containers", ("barrel", "crate", "chest", "container", "case", "box", "storage", "safe", "tent", "shelter")),
        ("clothes", ("clothing", "shirt", "jacket", "hoodie", "sweater", "shorts", "suit", "parka", "pants", "skirt", "dress", "boots", "shoes", "gloves", "helmet", "mask", "balaclava", "hat", "cap", "glasses", "vest", "belt", "bag", "backpack", "rucksack", "holster", "armband")),
        ("lootdispatch", ("lootdispatch", "loot_dispatch")),
        ("food", ("apple", "pear", "plum", "mushroom", "meat", "steak", "food", "sardines", "tuna", "beans", "peaches", "spaghetti", "cereal", "rice", "powderedmilk", "waterbottle", "canteen", "soda", "drink")),
        ("books", ("book", "paperback", "note", "journal")),
        ("weapons", ("rifle", "pistol", "shotgun", "smg", "weapon", "sword", "spear", "bow", "crossbow", "bayonet", "machete", "knife", "ammo")),
        ("tools", ("kit", "tool", "hammer", "shovel", "wrench", "pliers", "screwdriver", "saw", "pickaxe", "hatchet", "axe", "crowbar", "lockpick", "compass", "binoculars", "flashlight", "battery", "radio")),
    ]

    # Precedence: classname convention -> inheritance -> itemInfo[] -> section default.
    if classname_has_any(name, explosive_keywords):
        return finish("explosives")

    category = infer_category_from_keywords(name, vehicle_category_keywords)
    if category:
        return finish(category)

    if entry.base_class:
        if classname_has_any(entry.base_class, explosive_keywords):
            return finish("explosives")
        category = infer_category_from_keywords(entry.base_class, vehicle_category_keywords)
        if category:
            return finish(category)
        if class_lookup:
            base_key = entry.base_class.casefold()
            base_entry = class_lookup.get(base_key)
            if base_entry is not None and base_key not in visited:
                inherited_category = infer_config_type_category(base_entry, class_lookup, visited, category_cache)
                if inherited_category:
                    return finish(inherited_category)

    item_info = " ".join(parse_config_array_values(entry.body, "itemInfo"))
    if item_info:
        if classname_has_any(item_info, explosive_keywords):
            return finish("explosives")
        category = infer_category_from_keywords(item_info, vehicle_category_keywords)
        if category:
            return finish(category)

    if section in {"cfgweapons", "cfgmagazines"}:
        return finish("weapons")

    return finish("")


def config_class_entries_to_type_entries(entries: Iterable[ConfigClassEntry], source_path: str = "generated") -> list[TypeEntry]:
    type_entries: list[TypeEntry] = []
    config_entries = dedupe_config_class_entries(entries)
    class_lookup = build_config_class_lookup(config_entries)
    category_cache: dict[str, str] = {}
    for index, config_entry in enumerate(config_entries):
        if not is_public_config_class(config_entry):
            continue
        element = ET.Element("type", {"name": config_entry.name})
        for tag, value in [
            ("nominal", "0"),
            ("lifetime", "7200"),
            ("restock", "0"),
            ("min", "0"),
            ("quantmin", "-1"),
            ("quantmax", "-1"),
            ("cost", "100"),
        ]:
            child = ET.SubElement(element, tag)
            child.text = value
        ET.SubElement(
            element,
            "flags",
            {
                "count_in_cargo": "0",
                "count_in_hoarder": "0",
                "count_in_map": "1",
                "count_in_player": "0",
                "crafted": "0",
                "deloot": "0",
            },
        )
        category = config_entry.category_hint or infer_config_type_category(config_entry, class_lookup, category_cache=category_cache)
        if category:
            ET.SubElement(element, "category", {"name": category})
        type_entries.append(TypeEntry(config_entry.name, element, source_path, index))
    return type_entries


def dedupe_paths(paths: Iterable[str | os.PathLike[str]]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for path in paths:
        text = str(path)
        key = os.path.normcase(os.path.abspath(text))
        if key not in seen:
            seen.add(key)
            result.append(text)
    return result


def is_supported_config_file(path: str | os.PathLike[str]) -> bool:
    item = Path(path)
    suffix = item.suffix.casefold()
    if suffix in CONFIG_FILE_SUFFIXES:
        return True
    if suffix == ".xml":
        return True
    return False


def discover_config_files(
    roots: Iterable[str | os.PathLike[str]],
    handled_paths: Iterable[str | os.PathLike[str]] = (),
    recursive: bool = True,
) -> list[str]:
    handled = {
        os.path.normcase(os.path.abspath(str(path)))
        for path in handled_paths
    }
    paths: list[str] = []
    for root_value in roots:
        root = Path(root_value)
        if is_ignored_storage_path(root):
            continue
        if not root.is_dir():
            continue
        candidates = iter_files_ignoring_storage(root) if recursive else (path for path in root.iterdir() if path.name.casefold() != IGNORED_STORAGE_DIRNAME)
        for path in candidates:
            if not path.is_file():
                continue
            path_text = str(path)
            key = os.path.normcase(os.path.abspath(path_text))
            if key in handled:
                continue
            if is_supported_config_file(path):
                paths.append(path_text)
    return sorted(dedupe_paths(paths), key=str.casefold)


def validate_xml_files(paths: Iterable[str | os.PathLike[str]]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for path in dedupe_paths(paths):
        path = str(path)
        if is_ignored_storage_path(path):
            continue
        try:
            parse_xml_file(path)
        except ET.ParseError as exc:
            issues.append(create_xml_parse_issue(path, exc))
        except OSError as exc:
            issues.append(ValidationIssue("error", "", f"Could not read file: {exc}", path, "Check that the file exists, is not locked by another program, and that you have permission to read it."))
    return issues


def mission_xml_paths(root_path: str | os.PathLike[str]) -> list[str]:
    root = Path(root_path)
    if is_ignored_storage_path(root):
        return []
    if not root.is_dir():
        return []
    return dedupe_paths(sorted((str(path) for path in iter_files_ignoring_storage(root) if path.suffix.casefold() == ".xml"), key=str.casefold))


def validate_mission_workspace_xml(root_path: str | os.PathLike[str]) -> list[ValidationIssue]:
    return validate_xml_files(mission_xml_paths(root_path))


def discover_mission_workspace(root_path: str | os.PathLike[str]) -> MissionWorkspace:
    root = Path(root_path)
    issues: list[ValidationIssue] = []
    if is_ignored_storage_path(root):
        return MissionWorkspace(str(root), [], [], [], [], [], [], [], [], {}, [], [], {field: [] for field in RELATION_FIELDS}, [])
    if not root.is_dir():
        issue = ValidationIssue("error", "", f"Mission folder does not exist: {root}", str(root), "Choose the mission folder, for example an mpmissions/dayzOffline.* directory.")
        return MissionWorkspace(str(root), [], [], [], [], [], [], [], [], {}, [], [], {field: [] for field in RELATION_FIELDS}, [issue])

    type_paths: list[str] = []
    spawnable_type_paths: list[str] = []
    random_preset_paths: list[str] = []
    event_paths: list[str] = []
    event_spawn_paths: list[str] = []
    event_group_paths: list[str] = []
    territory_paths: list[str] = []
    cfgenvironment_paths: list[str] = []
    environment_territory_paths: dict[str, list[str]] = {}
    cfglimits_paths: list[str] = []
    cfgeconomycore_paths: list[str] = []
    relation_sets = {field: set() for field in RELATION_FIELDS}

    def load_cfglimits(path: Path) -> None:
        cfglimits_paths.append(str(path))
        definitions, parse_issues = parse_cfglimitsdefinition_file(path)
        issues.extend(parse_issues)
        for field, values in definitions.items():
            relation_sets[field].update(values)

    db_path = root / MISSION_DB_DIRNAME
    base_types_path = db_path / MISSION_TYPES_FILENAME
    if base_types_path.is_file():
        type_paths.append(str(base_types_path))
    else:
        issues.append(ValidationIssue("warning", "", "No db/types.xml found in mission folder.", str(root), "Choose the mission root that contains the DayZ economy db folder, or load loose XML files instead."))

    for base_spawnable_types_path in (root / MISSION_SPAWNABLE_TYPES_FILENAME, db_path / MISSION_SPAWNABLE_TYPES_FILENAME):
        if base_spawnable_types_path.is_file():
            spawnable_type_paths.append(str(base_spawnable_types_path))

    for base_random_presets_path in (root / MISSION_RANDOM_PRESETS_FILENAME, db_path / MISSION_RANDOM_PRESETS_FILENAME):
        if base_random_presets_path.is_file():
            random_preset_paths.append(str(base_random_presets_path))

    base_events_path = db_path / MISSION_EVENTS_FILENAME
    if base_events_path.is_file():
        event_paths.append(str(base_events_path))

    event_spawn_candidates: list[Path] = [
        root / MISSION_EVENT_SPAWNS_FILENAME,
        db_path / MISSION_EVENT_SPAWNS_FILENAME,
    ]
    for dirname in ("env", "events", "cfgeconomycore"):
        directory = root / dirname
        if directory.is_dir():
            event_spawn_candidates.extend(
                sorted(
                    (path for path in iter_files_ignoring_storage(directory) if path.name.casefold() == MISSION_EVENT_SPAWNS_FILENAME),
                    key=lambda item: str(item).casefold(),
                )
            )
    event_spawn_paths.extend(str(path) for path in event_spawn_candidates if path.is_file())

    for event_group_path in (root / MISSION_EVENT_GROUPS_FILENAME, db_path / MISSION_EVENT_GROUPS_FILENAME):
        if event_group_path.is_file():
            event_group_paths.append(str(event_group_path))

    environment_path = root / MISSION_ENVIRONMENT_FILENAME
    if environment_path.is_file():
        cfgenvironment_paths.append(str(environment_path))
        environment_paths, environment_map, environment_issues = parse_cfgenvironment_file(environment_path, root)
        territory_paths.extend(environment_paths)
        environment_territory_paths.update(environment_map)
        issues.extend(environment_issues)
    else:
        issues.append(ValidationIssue("info", "", "No cfgenvironment.xml found in mission folder.", str(root), "Add cfgenvironment.xml to map Animal, Ambient, and Infected events to their correct env territory files."))

    if not territory_paths:
        env_path = root / "env"
        if env_path.is_dir():
            territory_paths.extend(str(path) for path in sorted(env_path.glob(f"*{TERRITORY_FILENAME_SUFFIX}"), key=lambda item: str(item).casefold()))

    for cfglimits_path in dedupe_paths([root / MISSION_LIMITS_FILENAME, db_path / MISSION_LIMITS_FILENAME]):
        path = Path(cfglimits_path)
        if path.is_file():
            load_cfglimits(path)
    if not cfglimits_paths:
        issues.append(ValidationIssue("info", "", "No cfglimitsdefinition.xml found in mission folder root or db folder.", str(root), "Add cfglimitsdefinition.xml to unlock known category, tag, usage, and value lists."))

    economycore_path = root / MISSION_ECONOMYCORE_FILENAME
    if economycore_path.is_file():
        cfgeconomycore_paths.append(str(economycore_path))
        ce_type_paths, ce_issues = parse_cfgeconomycore_types(economycore_path, root)
        type_paths.extend(ce_type_paths)
        issues.extend(ce_issues)
        ce_spawnable_type_paths, ce_spawnable_issues = parse_cfgeconomycore_spawnabletypes(economycore_path, root)
        spawnable_type_paths.extend(ce_spawnable_type_paths)
        issues.extend(ce_spawnable_issues)
        ce_random_preset_paths, ce_random_preset_issues = parse_cfgeconomycore_randompresets(economycore_path, root)
        random_preset_paths.extend(ce_random_preset_paths)
        issues.extend(ce_random_preset_issues)
    else:
        issues.append(ValidationIssue("info", "", "No cfgeconomycore.xml found in mission folder.", str(root), "Add cfgeconomycore.xml if the mission uses CE folders with additional type files."))

    relation_definitions = {field: sorted(values, key=str.casefold) for field, values in relation_sets.items()}
    def allowed(paths):
        return dedupe_paths(path for path in paths if not is_ignored_storage_path(path))

    filtered_environment_paths = {}
    for name, paths in environment_territory_paths.items():
        filtered_paths = allowed(paths)
        if filtered_paths:
            filtered_environment_paths[name] = filtered_paths
    environment_territory_paths = filtered_environment_paths
    return MissionWorkspace(str(root), allowed(type_paths), allowed(spawnable_type_paths), allowed(random_preset_paths), allowed(event_paths), allowed(event_spawn_paths), allowed(event_group_paths), allowed(territory_paths), allowed(cfgenvironment_paths), environment_territory_paths, allowed(cfgeconomycore_paths), allowed(cfglimits_paths), relation_definitions, issues)


def create_xml_parse_issue(path: str, exc: ET.ParseError) -> ValidationIssue:
    line, column = getattr(exc, "position", (None, None))
    message = f"XML syntax error"
    if line is not None and column is not None:
        message += f" at line {line}, column {column}"
    message += f": {exc}"
    context = get_file_line_context(path, line, column)
    suggestion = "Fix the malformed XML at the shown line/column, then reload or validate again. Check for missing closing tags, broken quotes, and stray characters."
    return ValidationIssue("error", "", message, path, suggestion, line, column, context)


def get_file_line_context(path: str, line: int | None, column: int | None) -> str:
    if line is None or line < 1:
        return ""
    if is_ignored_storage_path(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as file:
            for index, text in enumerate(file, start=1):
                if index == line:
                    raw = text.rstrip("\r\n")
                    caret_column = max(0, column or 0)
                    return f"{raw}\n{' ' * caret_column}^"
    except OSError:
        return ""
    return ""


def load_types_files(paths: Iterable[str | os.PathLike[str]], include_duplicates: bool = True) -> tuple[list[TypeEntry], list[ValidationIssue]]:
    all_entries: list[TypeEntry] = []
    all_issues: list[ValidationIssue] = []
    for index, path in enumerate(paths):
        if is_ignored_storage_path(path):
            continue
        entries, issues = parse_types_file(path, index)
        all_entries.extend(entries)
        all_issues.extend(issues)
    if include_duplicates:
        all_issues.extend(validate_duplicates(all_entries))
    return all_entries, all_issues


def load_spawnable_types_files(paths: Iterable[str | os.PathLike[str]]) -> tuple[list[SpawnableTypeEntry], list[ValidationIssue]]:
    all_entries: list[SpawnableTypeEntry] = []
    all_issues: list[ValidationIssue] = []
    for index, path in enumerate(paths):
        if is_ignored_storage_path(path):
            continue
        entries, issues = parse_spawnable_types_file(path, index)
        all_entries.extend(entries)
        all_issues.extend(issues)
    return all_entries, all_issues


def load_random_presets_files(paths: Iterable[str | os.PathLike[str]]) -> tuple[list[RandomPresetEntry], list[ValidationIssue]]:
    all_entries: list[RandomPresetEntry] = []
    all_issues: list[ValidationIssue] = []
    for index, path in enumerate(paths):
        if is_ignored_storage_path(path):
            continue
        entries, issues = parse_random_presets_file(path, index)
        all_entries.extend(entries)
        all_issues.extend(issues)
    return all_entries, all_issues


def validate_spawnable_type_references(spawnable_entries: Iterable[SpawnableTypeEntry], type_entries: Iterable[TypeEntry]) -> list[ValidationIssue]:
    known_types = {entry.name.casefold() for entry in type_entries if entry.name and not entry.name.startswith("<missing ")}
    issues: list[ValidationIssue] = []
    if not known_types:
        return issues
    for entry in spawnable_entries:
        if entry.name.startswith("<missing "):
            continue
        if entry.name.casefold() not in known_types:
            issues.append(
                ValidationIssue(
                    "warning",
                    entry.name,
                    "Spawnable type is not present in loaded types.xml entries.",
                    entry.source_path,
                    "Add the parent classname to a loaded types file or remove the unused cfgspawnabletypes.xml entry.",
                )
            )
        for item_name in entry.referenced_item_names():
            if item_name.casefold() in known_types:
                continue
            issues.append(
                ValidationIssue(
                    "warning",
                    item_name,
                    f"Spawnable child item referenced by {entry.name} is not present in loaded types.xml entries.",
                    entry.source_path,
                    "Add the child classname to a loaded types file, fix the item name, or remove the reference.",
                )
            )
    return issues


def validate_random_preset_references(random_presets: Iterable[RandomPresetEntry], type_entries: Iterable[TypeEntry]) -> list[ValidationIssue]:
    known_types = {entry.name.casefold() for entry in type_entries if entry.name and not entry.name.startswith("<missing ")}
    issues: list[ValidationIssue] = []
    if not known_types:
        return issues
    for preset in random_presets:
        if preset.name.startswith("<missing "):
            continue
        for item_name in preset.referenced_item_names():
            if item_name.casefold() in known_types:
                continue
            issues.append(
                ValidationIssue(
                    "warning",
                    item_name,
                    f"Random preset item referenced by {preset.name} is not present in loaded types.xml entries.",
                    preset.source_path,
                    "Add the item classname to a loaded types file, fix the item name, or remove the reference.",
                )
            )
    return issues


def get_duplicate_groups(entries: Iterable[TypeEntry]) -> dict[str, list[TypeEntry]]:
    groups: dict[str, list[TypeEntry]] = {}
    for entry in entries:
        groups.setdefault(entry.name.casefold(), []).append(entry)
    return {items[0].name: items for items in groups.values() if len(items) > 1}


def validate_duplicates(entries: Iterable[TypeEntry]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for name, group in get_duplicate_groups(entries).items():
        sources = ", ".join(short_source(entry.source_path) for entry in group)
        signatures = {entry_signature(entry) for entry in group}
        if len(signatures) == 1:
            message = f"Duplicate classname appears {len(group)} times with identical data: {sources}"
            severity = "warning"
            suggestion = "Use Resolve duplicates to keep one copy, or leave it if you only loaded files for inspection."
        else:
            message = f"Conflicting duplicate classname appears {len(group)} times: {sources}"
            severity = "error"
            suggestion = "Use Resolve duplicates and choose the entry that should survive before exporting."
        issues.append(ValidationIssue(severity, name, message, group[0].source_path, suggestion))
    return issues


def validate_entries(
    entries: Iterable[TypeEntry],
    include_duplicates: bool = True,
    relation_definitions: dict[str, list[str]] | None = None,
) -> list[ValidationIssue]:
    entries = list(entries)
    issues: list[ValidationIssue] = []

    if include_duplicates:
        issues.extend(validate_duplicates(entries))

    for entry in entries:
        child_tags = [child.tag for child in entry.element]
        for required in ("lifetime", "flags"):
            if required not in child_tags:
                if required == "lifetime":
                    suggestion = "Add <lifetime>seconds</lifetime>. Use a short value for loose loot and a longer value for persistent placed objects."
                else:
                    suggestion = "Add a complete <flags count_in_cargo=\"0\" count_in_hoarder=\"0\" count_in_map=\"1\" count_in_player=\"0\" crafted=\"0\" deloot=\"0\"/> element."
                issues.append(ValidationIssue("error", entry.name, f"Missing <{required}>.", entry.source_path, suggestion))

        for flags in entry.element.findall("flags"):
            for field in FLAG_FIELDS:
                value = flags.attrib.get(field)
                if value is None:
                    issues.append(ValidationIssue("error", entry.name, f"<flags> missing {field}.", entry.source_path, f"Add {field}=\"0\" or {field}=\"1\" to the <flags> element."))
                elif value not in {"0", "1"}:
                    issues.append(ValidationIssue("error", entry.name, f"<flags {field}> must be 0 or 1, got {value!r}.", entry.source_path, f"Change {field} to either 0 or 1."))

        for field in NUMERIC_FIELDS:
            raw = entry.child_text(field)
            if raw == "":
                continue
            try:
                value = int(raw)
            except ValueError:
                issues.append(ValidationIssue("error", entry.name, f"<{field}> must be an integer, got {raw!r}.", entry.source_path, f"Replace <{field}> with a whole number."))
                continue
            if field in {"quantmin", "quantmax"} and value == -1:
                continue
            if value < 0:
                issues.append(ValidationIssue("error", entry.name, f"<{field}> cannot be negative.", entry.source_path, f"Use 0 or a positive whole number for <{field}>. Only quantmin/quantmax may use -1."))

        quantmin = parse_int(entry.child_text("quantmin"))
        quantmax = parse_int(entry.child_text("quantmax"))
        if quantmin is not None and quantmax is not None and quantmin > quantmax:
            issues.append(ValidationIssue("warning", entry.name, f"quantmin ({quantmin}) is higher than quantmax ({quantmax}).", entry.source_path, "Set quantmin lower than or equal to quantmax, or use -1/-1 for items without quantity."))

    issues.extend(validate_relation_definitions(entries, relation_definitions))
    return issues


def validate_relation_definitions(
    entries: Iterable[TypeEntry],
    relation_definitions: dict[str, list[str]] | None,
) -> list[ValidationIssue]:
    if not relation_definitions:
        return []

    known = {
        field: {value.casefold(): value for value in relation_definitions.get(field, []) if value}
        for field in RELATION_FIELDS
    }
    issues: list[ValidationIssue] = []

    for entry in entries:
        for field in RELATION_FIELDS:
            if not known[field]:
                continue
            for name in entry.relation_names(field):
                if name.casefold() in known[field]:
                    continue
                issue_message = f"Unknown {field} {name!r}; it is not defined in loaded cfglimitsdefinition.xml."
                suggestion = f"Use one of the known {field} values from cfglimitsdefinition.xml, or add {field} name=\"{name}\" there if this is intentional."
                issues.append(ValidationIssue("warning", entry.name, issue_message, entry.source_path, suggestion))

    return issues


def is_vanilla_infected_classname(name: str) -> bool:
    return name.startswith(("ZmbM", "ZmbF"))


def is_animal_classname(name: str) -> bool:
    return name.startswith("Animal")


def is_disabled_spawn_target(nominal: int | None, minimum: int | None) -> bool:
    return nominal == 0 and minimum == 0


def is_lifetime_only_entry(nominal_text: str, min_text: str) -> bool:
    return nominal_text == "" and min_text == ""


def should_skip_spawn_analysis(entry_name: str, nominal_text: str, min_text: str, nominal: int | None, minimum: int | None) -> bool:
    return (
        is_vanilla_infected_classname(entry_name)
        or is_animal_classname(entry_name)
        or is_disabled_spawn_target(nominal, minimum)
        or is_lifetime_only_entry(nominal_text, min_text)
    )


def analyze_economy(entries: Iterable[TypeEntry]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    for entry in entries:
        child_texts: dict[str, str] = {}
        relations = {field: [] for field in RELATION_FIELDS}
        flags = None

        for child in entry.element:
            text = (child.text or "").strip()
            if child.tag in NUMERIC_FIELDS:
                child_texts[child.tag] = text
            elif child.tag in relations:
                name = child.attrib.get("name", "")
                if name:
                    relations[child.tag].append(name)
            elif child.tag == "flags" and flags is None:
                flags = child

        nominal_text = child_texts.get("nominal", "")
        min_text = child_texts.get("min", "")
        lifetime_text = child_texts.get("lifetime", "")
        restock_text = child_texts.get("restock", "")
        nominal = parse_int(nominal_text)
        minimum = parse_int(min_text)
        lifetime = parse_int(lifetime_text)
        restock = parse_int(restock_text)
        categories = relations["category"]
        usages = relations["usage"]
        values = relations["value"]
        skip_spawn_analysis = should_skip_spawn_analysis(entry.name, nominal_text, min_text, nominal, minimum)

        if nominal_text == "" and not skip_spawn_analysis:
            issues.append(ValidationIssue("info", entry.name, "No nominal value. Valid for overrides or non-spawning/scripted objects; it does not define a natural spawn target by itself.", entry.source_path, "If this should spawn naturally, add <nominal>target_count</nominal>. If it is a placed/scripted object or partial override, leave it."))

        if min_text == "" and not skip_spawn_analysis:
            issues.append(ValidationIssue("info", entry.name, "No min value. Valid for overrides or non-spawning entries; CE will not get a separate minimum target from this entry.", entry.source_path, "If this should spawn naturally, set min to a sensible floor below nominal. Otherwise leave it."))
        elif nominal == 0 and minimum is not None and minimum > 0 and not skip_spawn_analysis:
            issues.append(ValidationIssue("warning", entry.name, f"nominal is 0 while min is {minimum}.", entry.source_path, "This is contradictory for normal natural spawning: nominal 0 disables the target, while min asks CE to maintain a minimum. Set min to 0 for disabled natural spawning, or raise nominal above 0."))
        elif nominal is not None and minimum is not None and nominal < minimum and not skip_spawn_analysis:
            issues.append(ValidationIssue("info", entry.name, f"nominal ({nominal}) is lower than min ({minimum}). This can be intentional, especially for disabled natural spawns or override files.", entry.source_path, "If this is normal loot, usually make nominal equal to or higher than min. If it is intentionally disabled or an override, leave it."))

        if restock_text == "" and not skip_spawn_analysis:
            issues.append(ValidationIssue("info", entry.name, "No restock value. Valid for overrides or entries that are not meant to naturally respawn from this file.", entry.source_path, "If this should spawn naturally, add <restock>seconds</restock>. Use 0 for immediate eligibility or a higher value for scarcity."))

        if lifetime is not None and lifetime > 31536000:
            issues.append(ValidationIssue("warning", entry.name, "lifetime is over one year. That may be intentional for persistent objects, but it is worth checking.", entry.source_path, "Lower lifetime for ordinary loot. Keep it high only for objects that should persist for a very long time."))

        if restock is not None and restock > 604800 and not skip_spawn_analysis:
            issues.append(ValidationIssue("warning", entry.name, "restock is over one week. That creates very slow natural replacement if this item spawns normally.", entry.source_path, "Lower restock if players should see this item return within normal play sessions."))

        if not categories and not skip_spawn_analysis:
            issues.append(ValidationIssue("info", entry.name, "No category. Valid for placed/scripted/override entries, but standalone natural loot usually needs a category for spawn point matching.", entry.source_path, "Add one <category name=\"...\"/> if this should spawn naturally. Leave blank for placed/scripted objects or partial overrides."))
        elif len(categories) > 1 and not skip_spawn_analysis:
            issues.append(ValidationIssue("warning", entry.name, f"Multiple categories defined ({', '.join(categories)}). Most normal loot entries use exactly one category.", entry.source_path, "Keep the one category that matches the intended spawn points unless you know this setup is supported by your CE configuration."))

        if flags is not None:
            if flags.attrib.get("crafted") == "1" and ((nominal is not None and nominal > 0) or (minimum is not None and minimum > 0)):
                issues.append(ValidationIssue("warning", entry.name, "crafted=1 while nominal or min is above 0.", entry.source_path, "crafted=1 prevents normal spawning. Use crafted=0 for ordinary loot, or set nominal/min to 0 if this is only crafted, placed, scripted, or otherwise created."))

    return issues


def parse_int(value: str) -> int | None:
    value = value.strip()
    if value == "":
        return None
    try:
        return int(value)
    except ValueError:
        return None


def parse_positive_int(value: str | None) -> int:
    if value is None:
        return 0
    try:
        return max(0, int(float(str(value).strip())))
    except (TypeError, ValueError):
        return 0


def parse_probability(value: str | None, default: float = 1.0) -> float:
    if value is None:
        return default
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return default
    if number < 0:
        return 0.0
    if number > 1:
        return number / 100.0 if number <= 100 else 1.0
    return number


def relation_values_from_element(element: ET.Element) -> dict[str, set[str]]:
    relations = {kind: set() for kind in RELATION_FIELDS}
    for kind in RELATION_FIELDS:
        attr_value = element.attrib.get(kind)
        if attr_value:
            relations[kind].add(attr_value.strip())
    for child in element.iter():
        if child.tag not in RELATION_FIELDS:
            continue
        name = child.attrib.get("name", "").strip()
        if name:
            relations[str(child.tag)].add(name)
    return relations


def parse_mapgrouppos_counts(path: str | os.PathLike[str]) -> Counter:
    root = parse_xml_file(path).getroot()
    counts: Counter = Counter()
    for element in root.iter():
        name = element.attrib.get("name", "").strip()
        if name:
            counts[name] += 1
    return counts


def parse_number_list(value: str, expected_count: int) -> bool:
    parts = str(value or "").strip().split()
    if len(parts) != expected_count:
        return False
    try:
        for part in parts:
            float(part)
    except ValueError:
        return False
    return True


def xml_element_to_text(element: ET.Element) -> str:
    clone = copy.deepcopy(element)
    ET.indent(clone, space="    ")
    return ET.tostring(clone, encoding="unicode").strip()


def parse_commented_xml_element(comment: ET.Element, expected_tag: str = "") -> ET.Element | None:
    if comment.tag is not ET.Comment:
        return None
    text = str(comment.text or "").strip()
    if not text:
        return None
    try:
        element = ET.fromstring(text)
    except ET.ParseError:
        return None
    if expected_tag and element.tag != expected_tag:
        return None
    return element


def mapgroupproto_element_records(parent: ET.Element, tag: str, inherited_commented: bool = False) -> list[tuple[ET.Element, bool]]:
    records: list[tuple[ET.Element, bool]] = []
    for child in list(parent):
        if child.tag == tag:
            records.append((child, inherited_commented))
            continue
        commented = parse_commented_xml_element(child, tag)
        if commented is not None:
            records.append((commented, True))
    return records


def mapgroupproto_match_count(relations: dict[str, tuple[str, ...]], entries: Iterable[TypeEntry]) -> int:
    values_by_kind = {kind: set(values) for kind, values in relations.items() if values}
    if not values_by_kind:
        return 0
    count = 0
    for entry in entries:
        matched = True
        for kind, values in values_by_kind.items():
            entry_values = set(entry.relation_names(kind))
            if entry_values and values.isdisjoint(entry_values):
                matched = False
                break
            if not entry_values:
                matched = False
                break
        if matched:
            count += 1
    return count


def mapgroupproto_relation_tuple(element: ET.Element, kind: str) -> tuple[str, ...]:
    values = []
    for child in element.findall(kind):
        name = child.attrib.get("name", "").strip()
        if name:
            values.append(name)
    return tuple(dict.fromkeys(values))


def validate_mapgroupproto_relation(
    issues: list[ValidationIssue],
    source_path: str,
    group_name: str,
    kind: str,
    value: str,
    definitions: dict[str, set[str]],
) -> int:
    allowed = definitions.get(kind, set())
    if allowed and value.casefold() not in {item.casefold() for item in allowed}:
        issues.append(
            ValidationIssue(
                "warning",
                group_name,
                f"Unknown {kind} value in mapgroupproto.xml: {value}",
                source_path,
                f"Keep it if this is intentional, but check cfglimitsdefinition.xml spelling. Unknown {kind} filters can make loot match zero items.",
            )
        )
        return 1
    return 0


def parse_mapgroupproto_file(
    path: str | os.PathLike[str],
    mapgrouppos_path: str | os.PathLike[str] | None = None,
    type_entries: Iterable[TypeEntry] = (),
    relation_definitions: dict[str, Iterable[str]] | None = None,
    collect_issues: bool = True,
) -> tuple[list[MapGroupProtoGroup], list[ValidationIssue]]:
    path = str(path)
    issues: list[ValidationIssue] = []
    try:
        parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
        tree = parse_xml_file(path, parser=parser)
    except ET.ParseError as exc:
        return [], [create_xml_parse_issue(path, exc)]
    except OSError as exc:
        return [], [ValidationIssue("error", "", f"Could not read file: {exc}", path, "Check that mapgroupproto.xml exists and can be read.")]

    root = tree.getroot()
    if collect_issues and root.tag.casefold() not in {"prototype", "mapgroupproto"}:
        issues.append(
            ValidationIssue(
                "error",
                "",
                f"Expected root <prototype> or <mapgroupproto>, got <{root.tag}>",
                path,
                "Load a DayZ mapgroupproto.xml file.",
            )
        )

    placement_counts: Counter = Counter()
    placed_names: set[str] = set()
    if mapgrouppos_path:
        try:
            placement_counts = parse_mapgrouppos_counts(mapgrouppos_path)
            placed_names = {str(name) for name in placement_counts}
        except (ET.ParseError, OSError) as exc:
            if collect_issues:
                issues.append(ValidationIssue("warning", "", f"Could not read mapgrouppos.xml: {exc}", str(mapgrouppos_path), "Placement counts will be unavailable."))

    definitions = {
        kind: {str(value) for value in values}
        for kind, values in (relation_definitions or {}).items()
    }
    entries = list(type_entries)
    names: Counter = Counter()
    groups: list[MapGroupProtoGroup] = []
    group_records = mapgroupproto_element_records(root, "group")
    if not group_records:
        group_records = [(element, False) for element in root.findall(".//group") if element.attrib.get("name", "").strip()]
    for index, (group, group_commented) in enumerate(group_records):
        group_issues_before = len(issues)
        name = group.attrib.get("name", "").strip()
        if not name:
            name = f"<missing group name #{index + 1}>"
            if collect_issues and not group_commented:
                issues.append(ValidationIssue("error", name, "Group is missing name attribute.", path, "Add the object/building classname to <group name=\"...\">."))
        if not group_commented:
            names[name] += 1
        lootmax_raw = group.attrib.get("lootmax", "0").strip()
        lootmax = parse_positive_int(lootmax_raw)
        if collect_issues and not group_commented and lootmax_raw and parse_int(lootmax_raw) is None:
            issues.append(ValidationIssue("warning", name, f"Invalid group lootmax: {lootmax_raw}", path, "lootmax should be an integer >= 0."))

        group_relations = {kind: mapgroupproto_relation_tuple(group, kind) for kind in RELATION_FIELDS}
        relation_issue_count = 0
        for kind, values in group_relations.items():
            for value in values:
                if collect_issues and not group_commented:
                    relation_issue_count += validate_mapgroupproto_relation(issues, path, name, kind, value, definitions)

        containers: list[MapGroupProtoContainer] = []
        container_lootmax_sum = 0
        point_total = 0
        active_point_total = 0
        active_container_count = 0
        for container_index, (container, container_commented) in enumerate(mapgroupproto_element_records(group, "container", group_commented)):
            container_issues_before = len(issues)
            if not container_commented:
                active_container_count += 1
            container_name = container.attrib.get("name", "").strip()
            if not container_name:
                container_name = f"<missing container name #{container_index + 1}>"
                if collect_issues and not container_commented:
                    issues.append(ValidationIssue("warning", name, "Container is missing name attribute.", path, "Name containers so they are easier to identify."))
            container_lootmax_raw = container.attrib.get("lootmax", "0").strip()
            container_lootmax = parse_positive_int(container_lootmax_raw)
            container_lootmax_sum += container_lootmax
            if collect_issues and not container_commented and container_lootmax_raw and parse_int(container_lootmax_raw) is None:
                issues.append(ValidationIssue("warning", name, f"Invalid container lootmax: {container_lootmax_raw}", path, "Container lootmax should be an integer >= 0."))

            relations = {kind: tuple(dict.fromkeys(group_relations.get(kind, ()) + mapgroupproto_relation_tuple(container, kind))) for kind in RELATION_FIELDS}
            for kind, values in relations.items():
                for value in values:
                    if collect_issues and not container_commented:
                        validate_mapgroupproto_relation(issues, path, name, kind, value, definitions)

            points: list[MapGroupProtoPoint] = []
            active_point_count = 0
            for point, point_commented in mapgroupproto_element_records(container, "point", container_commented):
                point_issues = 0
                if not point_commented:
                    active_point_count += 1
                pos = point.attrib.get("pos", "").strip()
                if collect_issues and not point_commented and not parse_number_list(pos, 3):
                    point_issues += 1
                    issues.append(ValidationIssue("warning", name, f"Point has invalid pos: {pos or '<missing>'}", path, "Point pos should contain exactly 3 numeric values: x y z."))
                for attr in ("range", "height"):
                    raw = point.attrib.get(attr, "").strip()
                    if collect_issues and not point_commented and raw:
                        try:
                            if float(raw) < 0:
                                raise ValueError
                        except ValueError:
                            point_issues += 1
                            issues.append(ValidationIssue("warning", name, f"Point has invalid {attr}: {raw}", path, f"Point {attr} should be a positive number."))
                points.append(
                    MapGroupProtoPoint(
                        pos=pos,
                        range=point.attrib.get("range", "").strip(),
                        height=point.attrib.get("height", "").strip(),
                        flags=point.attrib.get("flags", "").strip(),
                        issue_count=point_issues,
                        commented=point_commented,
                    )
                )
            point_count = len(points)
            point_total += point_count
            active_point_total += active_point_count
            if collect_issues and not container_commented and active_point_count <= 0:
                issues.append(ValidationIssue("warning", name, f"Container {container_name} has no point entries.", path, "A container without points cannot provide normal loot positions."))
            matching_items = mapgroupproto_match_count(relations, entries)
            if collect_issues and not container_commented and entries and matching_items <= 0:
                issues.append(ValidationIssue("warning", name, f"Container {container_name} filters match zero loaded type entries.", path, "Check category, usage, value, and tag spelling against Types and cfglimitsdefinition.xml."))
            containers.append(
                MapGroupProtoContainer(
                    name=container_name,
                    lootmax=container_lootmax,
                    point_count=point_count,
                    categories=relations["category"],
                    usages=relations["usage"],
                    values=relations["value"],
                    tags=relations["tag"],
                    matching_item_count=matching_items,
                    issue_count=len(issues) - container_issues_before,
                    points=tuple(points),
                    commented=container_commented,
                )
            )

        if collect_issues and not group_commented and active_container_count <= 0:
            issues.append(ValidationIssue("warning", name, "Group has no containers.", path, "A group without containers cannot provide normal filtered loot points."))
        if collect_issues and not group_commented and active_point_total > 0 and lootmax > active_point_total * 3:
            issues.append(ValidationIssue("warning", name, f"Group lootmax {lootmax} is much higher than {active_point_total} point(s).", path, "lootmax is a cap, not a way to create more physical spawn positions."))
        placed_count = int(placement_counts.get(name, 0))

        group_xml = xml_element_to_text(group)
        if group_commented:
            group_xml = f"<!--\n{group_xml}\n-->"

        groups.append(
            MapGroupProtoGroup(
                name=name,
                lootmax=lootmax,
                placed_count=placed_count,
                container_count=len(containers),
                point_count=point_total,
                container_lootmax_sum=container_lootmax_sum,
                categories=group_relations["category"],
                usages=group_relations["usage"],
                values=group_relations["value"],
                tags=group_relations["tag"],
                matching_item_count=sum(container.matching_item_count for container in containers),
                issue_count=len(issues) - group_issues_before + relation_issue_count,
                containers=tuple(containers),
                xml=group_xml,
                commented=group_commented,
            )
        )

    for name, count in names.items():
        if collect_issues and count > 1:
            issues.append(ValidationIssue("warning", name, f"Duplicate mapgroupproto group name appears {count} times.", path, "Duplicate prototypes can make edits ambiguous."))
    prototype_names = set(names)
    if collect_issues:
        for placed_name in sorted(placed_names - prototype_names, key=str.casefold)[:200]:
            issues.append(ValidationIssue("warning", placed_name, "mapgrouppos.xml has placed instance without matching mapgroupproto group.", str(mapgrouppos_path), "Add a matching prototype group or remove/fix the orphaned mapgrouppos.xml placement name."))
    return groups, issues


def parse_mapgroupproto_summaries(path: str | os.PathLike[str], placement_counts: Counter) -> tuple[LootMapGroupSummary, ...]:
    root = parse_xml_file(path).getroot()
    summaries: list[LootMapGroupSummary] = []
    for group in root.iter():
        if group.tag != "group":
            continue
        name = group.attrib.get("name", "").strip()
        if not name:
            continue
        building_count = int(placement_counts.get(name, 0))
        point_count = sum(1 for child in group.iter() if child.tag == "point")
        group_lootmax = parse_positive_int(group.attrib.get("lootmax"))
        descendant_lootmax = 0
        for child in group.iter():
            if child is group:
                continue
            descendant_lootmax += parse_positive_int(child.attrib.get("lootmax"))
        capacity = descendant_lootmax or group_lootmax or point_count
        relation_sets = relation_values_from_element(group)
        relations = {
            kind: tuple(sorted(values, key=str.casefold))
            for kind, values in relation_sets.items()
            if values
        }
        summaries.append(
            LootMapGroupSummary(
                name=name,
                building_count=building_count,
                spawnpoints_per_building=point_count,
                capacity_per_building=capacity,
                total_spawnpoints=point_count * building_count,
                total_capacity=capacity * building_count,
                relations=relations,
            )
        )
    return tuple(sorted(summaries, key=lambda item: item.name.casefold()))


def type_nominal_value(entry: TypeEntry) -> int:
    return parse_positive_int(entry.child_text("nominal", "0"))


def type_relation_values(entry: TypeEntry) -> dict[str, tuple[str, ...]]:
    return {kind: tuple(entry.relation_names(kind)) for kind in RELATION_FIELDS}


def type_flag_values(entry: TypeEntry) -> dict[str, int]:
    flags = entry.element.find("flags")
    result: dict[str, int] = {}
    for field_name in FLAG_FIELDS:
        result[field_name] = 1 if flags is not None and flags.attrib.get(field_name, "0").strip() == "1" else 0
    return result


def group_matches_type_relations(group: LootMapGroupSummary, item_relations: dict[str, tuple[str, ...]]) -> bool:
    matched_any = False
    for kind, item_values in item_relations.items():
        if not item_values:
            continue
        group_values = set(group.relations.get(kind, ()))
        if group_values:
            if group_values.isdisjoint(item_values):
                return False
            matched_any = True
    return matched_any


def matching_group_distribution(groups: Iterable[LootMapGroupSummary], kind: str) -> dict[str, int]:
    distribution: Counter = Counter()
    for group in groups:
        for value in group.relations.get(kind, ()):
            distribution[value] += group.total_spawnpoints or group.total_capacity
    return dict(sorted(distribution.items(), key=lambda item: (-item[1], item[0].casefold())))


def estimate_rarity_label(nominal: int, findability_score: float, rarity_index: float) -> str:
    if nominal <= 0 and findability_score <= 0:
        return "Not configured to spawn"
    if findability_score <= 0.0002 or rarity_index >= 5000:
        return "Unique / Extremely Rare"
    if findability_score <= 0.001 or rarity_index >= 1000:
        return "Very Rare"
    if findability_score <= 0.004 or rarity_index >= 250:
        return "Rare"
    if findability_score <= 0.0134 or rarity_index >= 75:
        return "Uncommon"
    if findability_score <= 0.0667 or rarity_index >= 15:
        return "Common"
    return "Very Common"


def random_preset_items_by_name(random_presets: Iterable[RandomPresetEntry]) -> dict[tuple[str, str], list[RandomPresetItem]]:
    result: dict[tuple[str, str], list[RandomPresetItem]] = {}
    for preset in random_presets:
        result[(preset.kind.casefold(), preset.name.casefold())] = list(preset.items)
    return result


def spawnabletype_derived_availability(
    spawnable_entries: Iterable[SpawnableTypeEntry],
    random_presets: Iterable[RandomPresetEntry],
    nominal_by_name: dict[str, int],
) -> tuple[dict[str, float], dict[str, set[str]]]:
    availability: dict[str, float] = Counter()
    sources: dict[str, set[str]] = {}
    preset_items = random_preset_items_by_name(random_presets)

    for parent in spawnable_entries:
        parent_nominal = max(nominal_by_name.get(parent.name.casefold(), 0), 0)
        if parent_nominal <= 0:
            continue
        for block in parent.blocks():
            block_chance = parse_probability(block.attributes.get("chance"), 1.0)
            items: list[SpawnableItem | RandomPresetItem] = list(block.items)
            preset_name = block.attributes.get("preset", "").strip()
            if preset_name:
                items.extend(preset_items.get((block.kind.casefold(), preset_name.casefold()), []))
            for item in items:
                item_name = item.name.strip()
                if not item_name:
                    continue
                item_chance = parse_probability(item.attributes.get("chance"), 1.0)
                value = parent_nominal * block_chance * item_chance
                if value <= 0:
                    continue
                key = item_name.casefold()
                availability[key] += value
                source_label = f"{block.kind} on {parent.name}"
                if preset_name:
                    source_label += f" via preset {preset_name}"
                sources.setdefault(key, set()).add(source_label)
    return dict(availability), sources


def event_child_findability(
    events: Iterable[EventEntry],
    event_spawn_positions: dict[str, list[EventSpawnPosition]] | None = None,
) -> tuple[dict[str, float], dict[str, set[str]]]:
    positions = event_spawn_positions or {}
    availability: dict[str, float] = Counter()
    sources: dict[str, set[str]] = {}
    for event in events:
        if not event.is_enabled():
            continue
        event_nominal = parse_positive_int(event.child_text("nominal", "0"))
        if event_nominal <= 0:
            continue
        children = event.element.findall("./children/child")
        if not children:
            continue
        weights = [max(parse_positive_int(child.attrib.get("max")), parse_positive_int(child.attrib.get("min")), 1) for child in children]
        total_weight = max(sum(weights), 1)
        position_count = len(positions.get(event.name.casefold(), []))
        event_availability = event_nominal / max(position_count, 1)
        for child, weight in zip(children, weights):
            child_type = child.attrib.get("type", "").strip()
            if not child_type:
                continue
            probability = weight / total_weight
            value = event_availability * probability
            key = child_type.casefold()
            availability[key] += value
            sources.setdefault(key, set()).add(f"event child {event.name}")
    return dict(availability), sources


def format_distribution_map(values: dict[str, int]) -> str:
    return "; ".join(f"{key}={value}" for key, value in values.items())


def loot_item_row_to_dict(row: LootItemRarityRow) -> dict[str, object]:
    return {
        "ClassName": row.class_name,
        "Nominal": row.nominal,
        "Min": row.minimum,
        "Lifetime": row.lifetime,
        "Restock": row.restock,
        "Cost": row.cost,
        "Category": row.category_text,
        "Usage": row.usage_text,
        "Value/Tier": row.value_text,
        "Tags": row.tag_text,
        "Flags": row.flag_text,
        "EligibleSpawnPoints": row.eligible_spawn_points,
        "LocationDensity": row.location_density,
        "PoolWeight": row.pool_weight,
        "HoardingSensitivity": row.hoarding_sensitivity,
        "EffectiveRarityScore": row.effective_rarity_score,
        "FindabilityScore": row.findability_score,
        "FindabilityPercent": row.findability_score * 100,
        "RarityIndex": row.rarity_index,
        "RarityView": f"1 per {row.rarity_index:.0f}",
        "EstimatedRarityLabel": row.estimated_rarity_label,
        "SpawnSources": row.spawn_source_text,
        "DistributionByUsage": format_distribution_map(row.distribution_by_usage),
        "DistributionByTier": format_distribution_map(row.distribution_by_tier),
        "DirectWorldFindability": row.direct_world_findability,
        "EventFindability": row.event_findability,
        "AttachmentAvailability": row.attachment_availability,
    }


def loot_distribution_item_rows_as_dicts(report: LootDistributionReport) -> list[dict[str, object]]:
    return [loot_item_row_to_dict(row) for row in report.item_rows]


def format_loot_distribution_csv(report: LootDistributionReport) -> str:
    rows = loot_distribution_item_rows_as_dicts(report)
    output = io.StringIO()
    if not rows:
        return ""
    writer = csv.DictWriter(output, fieldnames=list(rows[0].keys()), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def format_loot_distribution_json(report: LootDistributionReport) -> str:
    return json.dumps(
        {
            "limitation": "Estimated configured rarity/distribution from mission files. It cannot know live persistence, player inventories, hoarding, cleanup, restock timing, dynamic events, or current CE state.",
            "mapgroupproto": report.mapgroupproto_path,
            "mapgrouppos": report.mapgrouppos_path,
            "summary": {
                "map_group_count": report.map_group_count,
                "placed_group_count": report.placed_group_count,
                "total_capacity": report.total_capacity,
                "total_spawnpoints": report.total_spawnpoints,
                "total_nominal": report.total_nominal,
            },
            "items": loot_distribution_item_rows_as_dicts(report),
        },
        indent=2,
    )


def analyze_loot_distribution(
    entries: Iterable[TypeEntry],
    mapgroupproto_path: str | os.PathLike[str],
    mapgrouppos_path: str | os.PathLike[str],
    spawnable_entries: Iterable[SpawnableTypeEntry] = (),
    random_presets: Iterable[RandomPresetEntry] = (),
    events: Iterable[EventEntry] = (),
    event_spawn_positions: dict[str, list[EventSpawnPosition]] | None = None,
) -> LootDistributionReport:
    entries = list(entries)
    placement_counts = parse_mapgrouppos_counts(mapgrouppos_path)
    group_summaries = parse_mapgroupproto_summaries(mapgroupproto_path, placement_counts)
    capacity_by_relation: dict[tuple[str, str], dict[str, int]] = {}
    for group in group_summaries:
        if group.building_count <= 0:
            continue
        for kind, values in group.relations.items():
            for value in values:
                key = (kind, value)
                bucket = capacity_by_relation.setdefault(key, {"capacity": 0, "spawnpoints": 0, "buildings": 0})
                bucket["capacity"] += group.total_capacity
                bucket["spawnpoints"] += group.total_spawnpoints
                bucket["buildings"] += group.building_count

    nominal_by_relation: dict[tuple[str, str], int] = Counter()
    items_by_relation: dict[tuple[str, str], int] = Counter()
    unmatched_items: list[str] = []
    total_nominal = 0
    nominal_by_name = {entry.name.casefold(): type_nominal_value(entry) for entry in entries}
    attachment_availability, attachment_sources = spawnabletype_derived_availability(spawnable_entries, random_presets, nominal_by_name)
    event_availability, event_sources = event_child_findability(events, event_spawn_positions)
    item_rows: list[LootItemRarityRow] = []
    for entry in entries:
        nominal = type_nominal_value(entry)
        minimum = parse_positive_int(entry.child_text("min", "0"))
        lifetime = parse_positive_int(entry.child_text("lifetime", "0"))
        restock = parse_positive_int(entry.child_text("restock", "0"))
        cost = parse_positive_int(entry.child_text("cost", "100"))
        if nominal > 0:
            total_nominal += nominal
        relations = type_relation_values(entry)
        entry_keys = []
        for kind in RELATION_FIELDS:
            for value in relations[kind]:
                entry_keys.append((kind, value))
        if nominal > 0 and not entry_keys:
            unmatched_items.append(f"{entry.name}: nominal {nominal}, no category/usage/value/tag")
        matching_groups = [
            group
            for group in group_summaries
            if group.building_count > 0 and group_matches_type_relations(group, relations)
        ]
        eligible_spawn_points = sum(group.total_spawnpoints for group in matching_groups)
        if eligible_spawn_points <= 0:
            eligible_spawn_points = sum(group.total_capacity for group in matching_groups)
        matched_any = False
        for key in entry_keys:
            if nominal > 0:
                nominal_by_relation[key] += nominal
            items_by_relation[key] += 1
            if key in capacity_by_relation:
                matched_any = True
        if nominal > 0 and not matched_any:
            rels = ", ".join(f"{kind}={value}" for kind, value in entry_keys)
            unmatched_items.append(f"{entry.name}: nominal {nominal}, no mapgroupproto capacity matched ({rels})")
        flags = type_flag_values(entry)
        hoarding_sensitivity = flags.get("count_in_cargo", 0) + flags.get("count_in_hoarder", 0) + flags.get("count_in_player", 0)
        hoarding_penalty = 1 + hoarding_sensitivity
        global_rarity = 1 / max(nominal, 1)
        location_density = nominal / max(eligible_spawn_points, 1)
        pool_weight = nominal * max(cost, 1)
        effective_rarity_score = global_rarity * hoarding_penalty
        direct_world_findability = location_density / hoarding_penalty
        key = entry.name.casefold()
        derived_findability = attachment_availability.get(key, 0.0) / hoarding_penalty
        item_event_findability = event_availability.get(key, 0.0) / hoarding_penalty
        findability_score = direct_world_findability + derived_findability + item_event_findability
        rarity_index = 1 / max(findability_score, 0.000000001)
        spawn_sources = []
        if nominal > 0 and eligible_spawn_points > 0 and flags.get("crafted", 0) == 0:
            spawn_sources.append("World")
        if attachment_availability.get(key, 0.0) > 0:
            spawn_sources.extend(sorted(attachment_sources.get(key, ()), key=str.casefold))
        if event_availability.get(key, 0.0) > 0:
            spawn_sources.extend(sorted(event_sources.get(key, ()), key=str.casefold))
        if flags.get("crafted", 0):
            spawn_sources.append("Crafted")
        if flags.get("deloot", 0):
            spawn_sources.append("Deloot")
        if not spawn_sources:
            spawn_sources.append("No matched source")
        item_rows.append(
            LootItemRarityRow(
                class_name=entry.name,
                nominal=nominal,
                minimum=minimum,
                lifetime=lifetime,
                restock=restock,
                cost=cost,
                categories=relations["category"],
                usages=relations["usage"],
                values=relations["value"],
                tags=relations["tag"],
                flags=flags,
                eligible_spawn_points=eligible_spawn_points,
                location_density=location_density,
                pool_weight=pool_weight,
                hoarding_sensitivity=hoarding_sensitivity,
                effective_rarity_score=effective_rarity_score,
                findability_score=findability_score,
                rarity_index=rarity_index,
                estimated_rarity_label=estimate_rarity_label(nominal, findability_score, rarity_index),
                spawn_sources=tuple(dict.fromkeys(spawn_sources)),
                distribution_by_usage=matching_group_distribution(matching_groups, "usage"),
                distribution_by_tier=matching_group_distribution(matching_groups, "value"),
                direct_world_findability=direct_world_findability,
                event_findability=item_event_findability,
                attachment_availability=attachment_availability.get(key, 0.0),
            )
        )

    relation_keys = set(capacity_by_relation) | set(nominal_by_relation)
    relation_summaries = []
    for kind, value in sorted(relation_keys, key=lambda item: (item[0], item[1].casefold())):
        capacity = capacity_by_relation.get((kind, value), {})
        relation_summaries.append(
            LootRelationSummary(
                kind=kind,
                name=value,
                nominal=int(nominal_by_relation.get((kind, value), 0)),
                item_count=int(items_by_relation.get((kind, value), 0)),
                capacity=int(capacity.get("capacity", 0)),
                spawnpoints=int(capacity.get("spawnpoints", 0)),
                building_count=int(capacity.get("buildings", 0)),
            )
        )

    warnings = []
    unplaced_groups = [group.name for group in group_summaries if group.building_count <= 0]
    if unplaced_groups:
        warnings.append(f"{len(unplaced_groups)} mapgroupproto group(s) have no matching placement in mapgrouppos.xml.")
    if unmatched_items:
        warnings.append(f"{len(unmatched_items)} nominal item(s) have no matching map relation capacity.")

    return LootDistributionReport(
        mapgroupproto_path=str(mapgroupproto_path),
        mapgrouppos_path=str(mapgrouppos_path),
        map_group_count=len(group_summaries),
        placed_group_count=sum(1 for group in group_summaries if group.building_count > 0),
        total_capacity=sum(group.total_capacity for group in group_summaries),
        total_spawnpoints=sum(group.total_spawnpoints for group in group_summaries),
        total_nominal=total_nominal,
        relation_summaries=tuple(relation_summaries),
        item_rows=tuple(sorted(item_rows, key=lambda row: (-row.rarity_index, row.class_name.casefold()))),
        map_group_summaries=group_summaries,
        unmatched_items=tuple(unmatched_items),
        warnings=tuple(warnings),
    )


def format_loot_distribution_report(report: LootDistributionReport, limit: int = 40) -> str:
    lines = [
        "RaG Economy Manager Loot Distribution / Rarity report",
        "",
        "Summary",
        "-------",
        f"- mapgroupproto: {report.mapgroupproto_path}",
        f"- mapgrouppos: {report.mapgrouppos_path}",
        f"- Prototype groups: {report.map_group_count}",
        f"- Placed prototype groups: {report.placed_group_count}",
        f"- Total map loot capacity estimate: {report.total_capacity}",
        f"- Total map spawnpoints: {report.total_spawnpoints}",
        f"- Total loaded Types nominal demand: {report.total_nominal}",
        f"- Item rarity rows: {len(report.item_rows)}",
        "",
        "Important",
        "---------",
        "- This is an estimated configured rarity/distribution report, not a final CE simulator.",
        "- It cannot know live persistence, player inventories, hoarding, cleanup, restock timing, dynamic events, or current CE state.",
        "- Capacity comes from mapgroupproto lootmax/spawnpoints multiplied by mapgrouppos placement counts.",
        "- Demand comes from loaded Types nominal values plus derived cfgspawnabletypes/cfgrandompresets and events.xml child links.",
        "- Relation matching uses category, usage, value, and tag names.",
        "",
    ]
    if report.warnings:
        lines.extend(["Warnings", "--------"])
        lines.extend(f"- {warning}" for warning in report.warnings)
        lines.append("")

    rows = sorted(
        report.relation_summaries,
        key=lambda row: (
            row.status != "over target",
            row.status != "no capacity",
            -(row.ratio or 0),
            row.kind,
            row.name.casefold(),
        ),
    )
    lines.extend(["Relation capacity vs nominal demand", "-----------------------------------"])
    for row in rows[:limit]:
        ratio = "n/a" if row.ratio is None else f"{row.ratio:.2f}"
        lines.append(
            f"- {row.kind}:{row.name} | status={row.status} | nominal={row.nominal} | capacity={row.capacity} | ratio={ratio} | "
            f"items={row.item_count} | placed objects={row.building_count} | spawnpoints={row.spawnpoints}"
        )
    if len(rows) > limit:
        lines.append(f"- ... {len(rows) - limit} more relation row(s)")
    lines.append("")

    lines.extend(["Most constrained relations", "--------------------------"])
    constrained = [row for row in rows if row.nominal > 0 and row.capacity > 0]
    constrained.sort(key=lambda row: row.ratio or 0, reverse=True)
    for row in constrained[:15]:
        lines.append(f"- {row.kind}:{row.name}: nominal {row.nominal} / capacity {row.capacity} = {row.ratio:.2f} ({row.status})")
    if not constrained:
        lines.append("- No relation with both nominal demand and map capacity found.")
    lines.append("")

    lines.extend(["Estimated item rarity", "---------------------"])
    item_rows = sorted(report.item_rows, key=lambda row: (-row.rarity_index, row.class_name.casefold()))
    for row in item_rows[:limit]:
        lines.append(
            f"- {row.class_name} | {row.estimated_rarity_label} | nominal={row.nominal} | eligible points={row.eligible_spawn_points} | "
            f"findability={row.findability_score:.6f} | rarity index={row.rarity_index:.2f} | sources={row.spawn_source_text}"
        )
    if len(item_rows) > limit:
        lines.append(f"- ... {len(item_rows) - limit} more item row(s)")
    lines.append("")

    lines.extend(["Unmatched nominal items", "-----------------------"])
    if report.unmatched_items:
        for item in report.unmatched_items[:limit]:
            lines.append(f"- {item}")
        if len(report.unmatched_items) > limit:
            lines.append(f"- ... {len(report.unmatched_items) - limit} more unmatched item(s)")
    else:
        lines.append("- None found.")
    lines.append("")

    lines.extend(["Next useful checks", "------------------"])
    lines.append("- Check over-target relations first; these categories/usages likely have too much nominal demand for available map slots.")
    lines.append("- Check no-capacity relations; those items may never find valid map placement unless events/spawnabletypes handle them elsewhere.")
    lines.append("- Use this report before rarity scaling so balancing is based on actual map support.")
    return "\n".join(lines)


def short_source(path: str) -> str:
    return os.path.basename(path) or path


DAYZ_CRASH_CODES = {
    "C0000005": "access violation",
    "C0000374": "heap corruption",
    "C0000409": "stack buffer overrun / fast fail",
    "80000003": "breakpoint",
}


def dayz_text_file_kind(path: str | os.PathLike[str]) -> str:
    name = Path(path).name.lower()
    suffix = Path(path).suffix.lower()
    if suffix == ".rpt":
        return "RPT"
    if suffix == ".adm":
        return "ADM"
    if suffix == ".mdmp":
        return "Minidump"
    if name.startswith("script_") or name == "script.log":
        return "Script log"
    if name.startswith("crash_"):
        return "Crash log"
    if "console" in name:
        return "Console log"
    if suffix in {".log", ".txt"}:
        return "Log"
    return "File"


def _read_text_log(path: Path) -> tuple[list[str], DayZLogFinding | None]:
    if is_ignored_storage_path(path):
        return [], None
    try:
        return path.read_text(encoding="utf-8-sig", errors="replace").splitlines(), None
    except OSError as exc:
        return [], DayZLogFinding("error", "Could not read log file", str(exc), str(path))


def _context_lines(lines: list[str], start: int, count: int = 18) -> str:
    end = min(len(lines), start + count)
    return "\n".join(line.rstrip() for line in lines[start:end] if line.strip())


def _dayz_log_block_context(lines: list[str], start: int, count: int = 80) -> str:
    collected = []
    for line in lines[start:min(len(lines), start + count)]:
        clean = line.rstrip()
        lower = clean.strip().casefold()
        if collected and (lower.startswith("runtime mode") or lower.startswith("cli params:") or (lower and set(lower) <= {"-"})):
            break
        if clean.strip():
            collected.append(clean)
    return "\n".join(collected)


def _first_match_group(line: str, pattern: str) -> str:
    match = re.search(pattern, line, flags=re.IGNORECASE)
    return match.group(1).strip() if match else ""


def _dayz_script_mod_name(script_path: str) -> str:
    clean = str(script_path or "").replace("\\", "/").strip()
    if not clean:
        return ""
    parts = [part for part in clean.split("/") if part]
    if not parts:
        return ""
    if parts[0].endswith(":") and len(parts) > 1:
        parts = parts[1:]
    if _is_vanilla_dayz_script_path(clean):
        return "Vanilla DayZ"
    if parts and parts[0].casefold() in {"mpmissions", "missions", "dayz"} and len(parts) > 1:
        return parts[1]
    return parts[0].lstrip("@")


def _is_vanilla_dayz_script_path(script_path: str) -> bool:
    clean = str(script_path or "").replace("\\", "/").strip().casefold()
    return clean.startswith("scripts/")


def _script_stack_frame_from_line(line: str) -> dict[str, object] | None:
    clean = line.strip()
    match = re.search(r"(.+):(\d+)\s+Function\s+([A-Za-z0-9_<>~@#]+)", clean, flags=re.IGNORECASE)
    if not match:
        match = re.search(r"([A-Za-z0-9_@$:./\\ -]+\.(?:c|cpp|h|hpp)):(\d+)(?:\s+Function\s+([A-Za-z0-9_<>~@#]+))?", clean, flags=re.IGNORECASE)
    if not match:
        return None
    script_path = match.group(1).replace("\\", "/").strip()
    file_name = Path(script_path.rstrip("/")).name or script_path.rstrip("/").split("/")[-1]
    return {
        "path": script_path,
        "file": file_name,
        "row": int(match.group(2)),
        "function": (match.group(3) or "").strip(),
        "mod": _dayz_script_mod_name(script_path),
        "vanilla": _is_vanilla_dayz_script_path(script_path),
    }


def _likely_script_culprit_frame(frames: list[dict[str, object]]) -> dict[str, object] | None:
    if not frames:
        return None
    for frame in frames:
        if not frame.get("vanilla"):
            return frame
    return frames[0]


def _dayz_stack_crash_phase(frames: list[dict[str, object]]) -> str:
    joined = "\n".join(
        " ".join(str(frame.get(key) or "") for key in ("path", "function")).casefold()
        for frame in frames
    )
    if any(token in joined for token in ("createcustommission", "pluginmanagerinit", "pluginsinit", "missionbase", "oninit")):
        return "server startup / plugin initialization"
    if any(token in joined for token in ("eeinit", "onstoreload", "spawnobject", "spawnactivebuildings")):
        return "object spawn / initialization"
    return ""


def _extract_dayz_script_source(context: str) -> dict[str, object]:
    details: dict[str, object] = {
        "reason": "",
        "class": "",
        "function": "",
        "crash_function": "",
        "path": "",
        "file": "",
        "row": None,
        "mod": "",
        "frames": [],
        "phase": "",
    }
    for context_line in context.splitlines():
        clean = context_line.strip()
        lower = clean.casefold()
        if lower.startswith("reason:") and not details["reason"]:
            details["reason"] = clean.split(":", 1)[1].strip()
        elif lower.startswith("class:") and not details["class"]:
            details["class"] = clean.split(":", 1)[1].strip().strip("'")
        elif lower.startswith("function:") and not details["crash_function"]:
            details["crash_function"] = clean.split(":", 1)[1].strip().strip("'")
            details["function"] = details["crash_function"]
        frame = _script_stack_frame_from_line(clean)
        if frame is not None:
            details["frames"].append(frame)
    culprit = _likely_script_culprit_frame(details["frames"])
    if culprit is not None:
        details["path"] = str(culprit.get("path") or "")
        details["file"] = str(culprit.get("file") or "")
        details["row"] = culprit.get("row") if isinstance(culprit.get("row"), int) else None
        if culprit.get("function"):
            details["function"] = str(culprit.get("function") or "")
        details["mod"] = str(culprit.get("mod") or "")
    details["phase"] = _dayz_stack_crash_phase(details["frames"])
    return details


def analyze_dayz_text_log(path: str | os.PathLike[str]) -> tuple[list[DayZLogFinding], dict[str, Counter[str]], list[str]]:
    source = Path(path)
    lines, read_issue = _read_text_log(source)
    if read_issue is not None:
        return [read_issue], {"search_overtime": Counter(), "hard_to_place": Counter(), "lootmax_mismatch": Counter()}, []

    findings: list[DayZLogFinding] = []
    counters: dict[str, Counter[str]] = {
        "search_overtime": Counter(),
        "hard_to_place": Counter(),
        "lootmax_mismatch": Counter(),
    }
    timeline: list[str] = []
    crash_codes_seen: set[str] = set()
    vm_exception_lines: set[int] = set()
    script_signature_seen: set[tuple[str, str, str, str]] = set()

    def add_script_finding(severity: str, title: str, message: str, line_number: int, context: str, suggestion: str = "", related_name: str = "") -> None:
        script_details = _extract_dayz_script_source(context)
        signature = (
            title,
            str(script_details.get("mod") or ""),
            str(script_details.get("file") or ""),
            str(script_details.get("function") or ""),
        )
        if signature in script_signature_seen:
            return
        script_signature_seen.add(signature)
        findings.append(DayZLogFinding(
            severity,
            title,
            message,
            str(source),
            line_number,
            context,
            suggestion=suggestion,
            reason=str(script_details.get("reason") or ""),
            script_class=str(script_details.get("class") or ""),
            crash_function_name=str(script_details.get("crash_function") or ""),
            mod_name=str(script_details.get("mod") or ""),
            script_file=str(script_details.get("file") or ""),
            function_name=str(script_details.get("function") or ""),
            row=script_details.get("row") if isinstance(script_details.get("row"), int) else None,
            location=str(script_details.get("path") or ""),
            related_name=related_name,
            crash_phase=str(script_details.get("phase") or ""),
            stack_frames=script_details.get("frames") if isinstance(script_details.get("frames"), list) else [],
        ))

    for index, line in enumerate(lines):
        line_no = index + 1
        stripped = line.strip()
        lower = stripped.casefold()

        if "virtual machine exception" in lower:
            context = _dayz_log_block_context(lines, index, 80)
            script_details = _extract_dayz_script_source(context)
            reason = str(script_details.get("reason") or "")
            script_class = str(script_details.get("class") or "")
            function = str(script_details.get("function") or "")
            crash_function = str(script_details.get("crash_function") or function)
            readable_parts = []
            if reason:
                readable_parts.append(reason)
            if script_class:
                readable_parts.append(f"class {script_class}")
            if crash_function:
                readable_parts.append(f"function {crash_function}")
            message = "The script engine stopped because " + ", ".join(readable_parts) + "." if readable_parts else "A DayZ script exception was logged. Inspect the stack trace and fix the referenced mod/script."
            findings.append(DayZLogFinding(
                "error",
                "Script VM exception",
                message,
                str(source),
                line_no,
                context,
                reason=reason,
                script_class=script_class,
                crash_function_name=crash_function,
                mod_name=str(script_details.get("mod") or ""),
                script_file=str(script_details.get("file") or ""),
                function_name=function,
                row=script_details.get("row") if isinstance(script_details.get("row"), int) else None,
                location=str(script_details.get("path") or ""),
                crash_phase=str(script_details.get("phase") or ""),
                stack_frames=script_details.get("frames") if isinstance(script_details.get("frames"), list) else [],
            ))
            vm_exception_lines.update(range(line_no, min(len(lines), index + 24) + 1))
            continue

        if lower == "stack overflow" or "stack overflow" in lower:
            add_script_finding(
                "error",
                "Script stack overflow",
                "A script call chain became too deep and overflowed the script stack. This is usually caused by too many chained overrides or recursive action registration.",
                line_no,
                _dayz_log_block_context(lines, index, 140),
                "Reduce or isolate the mods in the listed RegisterActions chain. Start with the first non-vanilla mod frame shown as the likely source.",
            )
            continue

        if "particle file not found" in lower:
            particle_path = _first_match_group(stripped, r"<([^>]+)>")
            add_script_finding(
                "warning",
                "Missing particle file",
                f"A mod references a particle file that is not present: {particle_path or 'unknown particle'}.",
                line_no,
                _dayz_log_block_context(lines, index, 30),
                "Fix the particle path in the mod script or remove the bad particle reference.",
                related_name=particle_path,
            )
            continue

        if "cast failed" in lower and "effectarealoader" in lower:
            add_script_finding(
                "warning",
                "Contaminated area cast failed",
                "A configured contaminated area class could not be cast to EffectArea. This usually means a typo, missing class, wrong inheritance, or bad zone config.",
                line_no,
                _dayz_log_block_context(lines, index, 50),
                "Check the contaminated area configuration and the first non-vanilla OnMissionStart frame.",
            )
            continue

        if "action target not created" in lower:
            action_class = ""
            action_context = _dayz_log_block_context(lines, index, 40)
            for context_line in action_context.splitlines():
                if context_line.strip().casefold().startswith("class:"):
                    action_class = context_line.split(":", 1)[1].strip().strip("'")
                    break
            add_script_finding(
                "warning",
                f"Action target not created{f' ({action_class})' if action_class else ''}",
                f"An action could not create its target object{f' for {action_class}' if action_class else ''}. Repeated lines can point to a broken action mod or an invalid action target.",
                line_no,
                action_context,
                "Check the likely source mod and any lock/vehicle/action mods involved in the stack.",
                related_name=action_class,
            )
            continue

        code_match = re.search(r"(?:Exception code:|ExceptionCode|SEH exception thrown\. Exception code:)\s*0?x?([0-9A-Fa-f]{8})", stripped)
        if code_match:
            code = code_match.group(1).upper()
            if code not in crash_codes_seen:
                label = DAYZ_CRASH_CODES.get(code, "native exception")
                severity = "error" if code in {"C0000005", "C0000374", "C0000409"} else "warning"
                findings.append(DayZLogFinding(severity, f"Native crash {code}", f"DayZ reported {label}. This needs the matching RPT/minidump and usually cannot be fully proven from text logs alone.", str(source), line_no, stripped))
                crash_codes_seen.add(code)

        if "heap has been corrupted" in lower or "ein heap wurde beschädigt" in lower:
            if "C0000374" not in crash_codes_seen:
                findings.append(DayZLogFinding("error", "Native heap corruption", "DayZ detected corrupted heap memory. The bad write often happened before the crash line; shutdown/free only exposed it.", str(source), line_no, stripped))
                crash_codes_seen.add("C0000374")

        if "null pointer to instance" in lower and line_no not in vm_exception_lines:
            context = _dayz_log_block_context(lines, max(0, index - 2), 40)
            script_details = _extract_dayz_script_source(context)
            findings.append(DayZLogFinding(
                "error",
                "Script null pointer",
                "A script tried to use an object instance that was not created or was already gone.",
                str(source),
                line_no,
                context,
                reason=str(script_details.get("reason") or "NULL pointer to instance"),
                script_class=str(script_details.get("class") or ""),
                crash_function_name=str(script_details.get("crash_function") or ""),
                mod_name=str(script_details.get("mod") or ""),
                script_file=str(script_details.get("file") or ""),
                function_name=str(script_details.get("function") or ""),
                row=script_details.get("row") if isinstance(script_details.get("row"), int) else None,
                location=str(script_details.get("path") or ""),
                crash_phase=str(script_details.get("phase") or ""),
                stack_frames=script_details.get("frames") if isinstance(script_details.get("frames"), list) else [],
            ))

        if 'cannot open file "" for reading' in lower:
            findings.append(DayZLogFinding("warning", "Empty file path read", "A script tried to open an empty file path. This usually means a missing or bad config value.", str(source), line_no, stripped))

        if "causing search overtime" in lower:
            item = _first_match_group(stripped, r'causing search overtime:\s*"([^"]+)"')
            if item:
                counters["search_overtime"][item] += 1

        if "hard to place, performance drops" in lower:
            item = _first_match_group(stripped, r'hard to place, performance drops:\s*"([^"]+)"')
            if item:
                counters["hard_to_place"][item] += 1

        if "sum of container lootmax is lower" in lower or "wanting to spawn more loot than possible" in lower:
            item = _first_match_group(stripped, r"Type:\s*([^:]+?)\s*::")
            counters["lootmax_mismatch"][item or "Unknown static event child"] += 1

        if "#shutdown" in lower or "command received: #shutdown" in lower:
            timeline.append(f"{dayz_text_file_kind(source)} {line_no}: shutdown command accepted")
        elif "server is restarting" in lower:
            timeline.append(f"{dayz_text_file_kind(source)} {line_no}: restart warning")
        elif "onmissionfinish" in lower:
            timeline.append(f"{dayz_text_file_kind(source)} {line_no}: mission finish started")
        elif "cleaning up script module globals" in lower:
            timeline.append(f"{dayz_text_file_kind(source)} {line_no}: script globals cleanup")
        elif "engine" in lower and "crashed" in lower:
            timeline.append(f"{dayz_text_file_kind(source)} {line_no}: engine crash line")

    if counters["search_overtime"]:
        top_item, top_count = counters["search_overtime"].most_common(1)[0]
        total = sum(counters["search_overtime"].values())
        findings.append(DayZLogFinding("warning", "CE loot search overtime spam", f"{total} overtime line(s). Top offender: {top_item} ({top_count}). This usually means loot has nowhere valid to spawn or nominal/restock pressure is too high.", str(source), related_name=top_item))

    if counters["hard_to_place"]:
        top_item, top_count = counters["hard_to_place"].most_common(1)[0]
        total = sum(counters["hard_to_place"].values())
        findings.append(DayZLogFinding("warning", "Hard-to-place loot", f"{total} hard-to-place line(s). Top offender: {top_item} ({top_count}). This can hurt server performance.", str(source), related_name=top_item))

    if counters["lootmax_mismatch"]:
        top_item, top_count = counters["lootmax_mismatch"].most_common(1)[0]
        findings.append(DayZLogFinding("warning", "Static loot max mismatch", f"{top_item} has child lootmax higher than its container capacity ({top_count} line(s)). Fix cfgspawnabletypes/event child loot values.", str(source), related_name=top_item))

    return findings, counters, timeline


def _safe_rva_slice(data: bytes, rva: int, size: int) -> bytes:
    if rva < 0 or size < 0 or rva + size > len(data):
        return b""
    return data[rva : rva + size]


def _read_minidump_utf16_string(data: bytes, rva: int) -> str:
    header = _safe_rva_slice(data, rva, 4)
    if len(header) < 4:
        return ""
    byte_count = struct.unpack_from("<I", header)[0]
    raw = _safe_rva_slice(data, rva + 4, byte_count)
    if not raw:
        return ""
    return raw.decode("utf-16-le", errors="replace").rstrip("\x00")


def analyze_dayz_minidump(path: str | os.PathLike[str]) -> list[DayZLogFinding]:
    source = Path(path)
    if is_ignored_storage_path(source):
        return []
    try:
        data = source.read_bytes()
    except OSError as exc:
        return [DayZLogFinding("error", "Could not read minidump", str(exc), str(source))]
    if len(data) < 32 or data[:4] != b"MDMP":
        return [DayZLogFinding("warning", "Not a Windows minidump", "The file does not start with the MDMP signature.", str(source))]

    try:
        _signature, _version, stream_count, stream_rva = struct.unpack_from("<IIII", data, 0)
        streams: dict[int, tuple[int, int]] = {}
        for index in range(stream_count):
            entry_offset = stream_rva + index * 12
            if entry_offset + 12 > len(data):
                continue
            stream_type, data_size, rva = struct.unpack_from("<III", data, entry_offset)
            streams[stream_type] = (rva, data_size)
    except struct.error as exc:
        return [DayZLogFinding("warning", "Minidump parse failed", f"Could not read minidump stream directory: {exc}", str(source))]

    modules: list[tuple[int, int, str]] = []
    module_stream = streams.get(4)
    if module_stream:
        rva, size = module_stream
        raw = _safe_rva_slice(data, rva, size)
        if len(raw) >= 4:
            try:
                module_count = struct.unpack_from("<I", raw, 0)[0]
                entry_size = 108
                for index in range(min(module_count, 4096)):
                    entry_offset = 4 + index * entry_size
                    if entry_offset + entry_size > len(raw):
                        break
                    base = struct.unpack_from("<Q", raw, entry_offset)[0]
                    image_size = struct.unpack_from("<I", raw, entry_offset + 8)[0]
                    name_rva = struct.unpack_from("<I", raw, entry_offset + 20)[0]
                    name = _read_minidump_utf16_string(data, name_rva)
                    modules.append((base, image_size, Path(name).name if name else "unknown module"))
            except struct.error:
                modules = []

    exception_stream = streams.get(6)
    if not exception_stream:
        return [DayZLogFinding("hint", "Minidump loaded", "Valid minidump found, but no exception stream was present.", str(source))]

    rva, size = exception_stream
    raw = _safe_rva_slice(data, rva, size)
    if len(raw) < 32:
        return [DayZLogFinding("warning", "Minidump exception stream unreadable", "The exception stream is shorter than expected.", str(source))]

    try:
        thread_id = struct.unpack_from("<I", raw, 0)[0]
        code = struct.unpack_from("<I", raw, 8)[0]
        address = struct.unpack_from("<Q", raw, 24)[0]
    except struct.error as exc:
        return [DayZLogFinding("warning", "Minidump exception parse failed", str(exc), str(source))]

    module_name = ""
    module_offset = 0
    for base, image_size, name in modules:
        if base <= address < base + image_size:
            module_name = name
            module_offset = address - base
            break

    code_hex = f"{code:08X}"
    label = DAYZ_CRASH_CODES.get(code_hex, "native exception")
    message = f"Exception {code_hex} ({label}) on thread {thread_id}, address 0x{address:X}."
    if module_name:
        message += f" Address maps to {module_name}+0x{module_offset:X}."
    else:
        message += " No loaded module mapping was found for the exception address."
    return [DayZLogFinding("error" if code_hex.startswith("C") else "warning", "Minidump exception", message, str(source))]


def analyze_dayz_session_logs(paths: Iterable[str | os.PathLike[str]]) -> DayZLogAnalysis:
    files: list[str] = []
    findings: list[DayZLogFinding] = []
    counter_totals: dict[str, Counter[str]] = {
        "search_overtime": Counter(),
        "hard_to_place": Counter(),
        "lootmax_mismatch": Counter(),
    }
    timeline: list[str] = []

    for raw_path in paths:
        if not raw_path:
            continue
        path = Path(raw_path)
        files.append(str(path))
        if not path.exists():
            findings.append(DayZLogFinding("error", "File does not exist", "The selected file could not be found.", str(path)))
            continue
        if path.suffix.lower() == ".mdmp":
            findings.extend(analyze_dayz_minidump(path))
            continue
        text_findings, counters, events = analyze_dayz_text_log(path)
        findings.extend(text_findings)
        timeline.extend(events)
        for key, counter in counters.items():
            counter_totals[key].update(counter)

    if not files:
        findings.append(DayZLogFinding("warning", "No files selected", "Choose RPT, ADM, script/crash/server logs, or MDMP files from one server session."))

    severity_order = {"error": 0, "warning": 1, "hint": 2}
    issue_order = {
        "script vm exception": 0,
        "script null pointer": 1,
        "native heap corruption": 5,
        "native crash c0000374": 5,
        "minidump exception": 6,
    }
    findings.sort(key=lambda finding: (
        severity_order.get(finding.severity, 3),
        issue_order.get(finding.title.casefold(), 4),
        finding.source_path,
        finding.line or 0,
        finding.title.casefold(),
    ))
    compact_counters = {
        key: [(name, count) for name, count in counter.most_common(50)]
        for key, counter in counter_totals.items()
    }
    return DayZLogAnalysis(files=files, findings=findings, counters=compact_counters, session_events=timeline[-40:])


def default_windows_debugger_candidates() -> list[Path]:
    candidates: list[Path] = []
    for root in (
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        os.environ.get("ProgramFiles", r"C:\Program Files"),
    ):
        if not root:
            continue
        candidates.append(Path(root) / "Windows Kits" / "10" / "Debuggers" / "x64" / "cdb.exe")
    which = shutil.which("cdb.exe")
    if which:
        candidates.append(Path(which))
    return candidates


def find_windows_cdb(candidates: Iterable[str | os.PathLike[str]] | None = None) -> str:
    for candidate in list(candidates or []) + default_windows_debugger_candidates():
        path = Path(candidate)
        if path.is_file():
            return str(path)
    return ""


def default_dayz_image_paths() -> list[str]:
    paths = []
    for candidate in (
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Steam" / "steamapps" / "common" / "DayZServer",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Steam" / "steamapps" / "common" / "DayZ",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Steam" / "steamapps" / "common" / "DayZServer",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Steam" / "steamapps" / "common" / "DayZ",
    ):
        if candidate.is_dir():
            paths.append(str(candidate))
    return paths


def extract_dayz_debugger_highlights(output: str) -> list[str]:
    highlights: list[str] = []
    stack_lines: list[str] = []
    capture_stack = False
    for raw_line in output.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            if capture_stack and stack_lines:
                capture_stack = False
            continue
        for prefix in (
            "PROCESS_NAME:",
            "ERROR_CODE:",
            "EXCEPTION_CODE_STR:",
            "FAILURE_BUCKET_ID:",
            "FAULTING_THREAD:",
            "IMAGE_NAME:",
            "MODULE_NAME:",
        ):
            if stripped.startswith(prefix):
                highlights.append(stripped)
                break
        if stripped.startswith("ExceptionAddress:") or stripped.startswith("ExceptionCode:"):
            highlights.append(stripped)
        if stripped.startswith("STACK_TEXT:"):
            capture_stack = True
            continue
        if capture_stack and len(stack_lines) < 12:
            if "!" in stripped or "DayZServer_x64+" in stripped or "ucrtbase" in stripped or "ntdll" in stripped:
                stack_lines.append(stripped)
    if stack_lines:
        highlights.append("Stack:")
        highlights.extend(stack_lines)
    seen = set()
    unique = []
    for line in highlights:
        key = line.casefold()
        if key not in seen:
            seen.add(key)
            unique.append(line)
    return unique


def run_dayz_minidump_debugger(
    dump_path: str | os.PathLike[str],
    cdb_path: str | os.PathLike[str] = "",
    symbol_dir: str | os.PathLike[str] = "",
    image_paths: Iterable[str | os.PathLike[str]] | None = None,
    timeout: int = 120,
) -> DayZDebuggerResult:
    dump = Path(dump_path)
    debugger = str(cdb_path or find_windows_cdb())
    if not debugger:
        return DayZDebuggerResult(str(dump), "", False, False, "", "Windows Debugging Tools cdb.exe was not found.")
    if not dump.is_file():
        return DayZDebuggerResult(str(dump), debugger, False, False, "", "Minidump file does not exist.")

    symbols = Path(symbol_dir) if symbol_dir else Path(tempfile.gettempdir()) / "rag_economy_manager_symbols"
    try:
        symbols.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return DayZDebuggerResult(str(dump), debugger, False, False, "", f"Could not create symbol cache: {exc}")

    image_path_values = [str(Path(path)) for path in (image_paths or []) if path]
    image_path_values.extend(default_dayz_image_paths())
    image_path_values = list(dict.fromkeys(path for path in image_path_values if Path(path).is_dir()))
    command = (
        ".reload /f ntdll.dll; "
        ".reload /f ucrtbase.dll; "
        ".reload /f DayZServer_x64.exe; "
        "!analyze -v; "
        ".exr -1; "
        ".ecxr; "
        "kv; "
        "~* k; "
        "lmvm ntdll; "
        "lmvm ucrtbase; "
        "lmvm DayZServer_x64; "
        "q"
    )
    args = [
        debugger,
        "-y",
        f"srv*{symbols}*https://msdl.microsoft.com/download/symbols",
    ]
    if image_path_values:
        args.extend(["-i", ";".join(image_path_values)])
    args.extend(["-z", str(dump), "-c", command])
    try:
        completed = subprocess.run(args, capture_output=True, text=True, errors="replace", timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or "") + (exc.stderr or "")
        return DayZDebuggerResult(str(dump), debugger, False, True, output, f"Debugger timed out after {timeout} seconds.")
    except OSError as exc:
        return DayZDebuggerResult(str(dump), debugger, False, False, "", f"Could not run debugger: {exc}")

    output = (completed.stdout or "") + (completed.stderr or "")
    success = completed.returncode == 0 and bool(output.strip())
    error = "" if success else f"Debugger exited with code {completed.returncode}."
    return DayZDebuggerResult(str(dump), debugger, success, False, output, error)


def create_dayz_debugger_report(results: Iterable[DayZDebuggerResult]) -> str:
    result_list = list(results)
    lines = ["RaG Economy Manager deep minidump report", ""]
    if not result_list:
        lines.append("No minidumps were analyzed.")
        return "\n".join(lines).rstrip() + "\n"
    for index, result in enumerate(result_list):
        if index:
            lines.append("")
            lines.append("=" * 80)
            lines.append("")
        lines.append(result.to_text().rstrip())
    return "\n".join(lines).rstrip() + "\n"


def entry_signature(entry: TypeEntry) -> tuple:
    return tuple(canonical_child(child) for child in entry.element)


def canonical_child(child: ET.Element) -> tuple:
    return (
        child.tag,
        tuple(sorted(child.attrib.items())),
        (child.text or "").strip(),
        tuple(canonical_child(grandchild) for grandchild in child),
    )


def entry_summary(entry: TypeEntry) -> dict[str, tuple | str]:
    summary: dict[str, tuple | str] = {"name": entry.name}
    for field in NUMERIC_FIELDS:
        summary[field] = entry.child_text(field)
    for field in RELATION_FIELDS:
        summary[field] = tuple(sorted(entry.relation_names(field)))
    summary["flags"] = tuple(sorted(tuple(sorted(child.attrib.items())) for child in entry.element.findall("flags")))
    return summary


def compare_entries(old_entries: Iterable[TypeEntry], new_entries: Iterable[TypeEntry]) -> list[CompareChange]:
    old_map = unique_by_name(old_entries)
    new_map = unique_by_name(new_entries)
    changes: list[CompareChange] = []

    for name in sorted(set(new_map) - set(old_map), key=str.casefold):
        changes.append(CompareChange(new_map[name].name, "added", []))
    for name in sorted(set(old_map) - set(new_map), key=str.casefold):
        changes.append(CompareChange(old_map[name].name, "removed", []))

    for key in sorted(set(old_map) & set(new_map), key=str.casefold):
        old_summary = entry_summary(old_map[key])
        new_summary = entry_summary(new_map[key])
        fields = [field for field in sorted(set(old_summary) | set(new_summary)) if old_summary.get(field) != new_summary.get(field)]
        fields = [field for field in fields if field != "name"]
        if fields:
            changes.append(CompareChange(new_map[key].name, "changed", fields))
    return changes


def create_change_report(original_entries: Iterable[TypeEntry], current_entries: Iterable[TypeEntry]) -> str:
    original_groups = group_entries_by_name(original_entries)
    current_groups = group_entries_by_name(current_entries)
    lines = ["RaG Economy Manager change report", ""]
    changed_any = False

    for key in sorted(set(original_groups) | set(current_groups), key=str.casefold):
        original_group = original_groups.get(key, [])
        current_group = current_groups.get(key, [])
        display_name = (current_group or original_group)[0].name

        if not original_group:
            changed_any = True
            lines.append(f"ADDED: {display_name}")
            for entry in current_group:
                lines.append(f"  + {entry_descriptor(entry)}")
            lines.append("")
            continue

        if not current_group:
            changed_any = True
            lines.append(f"REMOVED: {display_name}")
            for entry in original_group:
                lines.append(f"  - {entry_descriptor(entry)}")
            lines.append("")
            continue

        if len(original_group) != len(current_group):
            changed_any = True
            lines.append(f"COUNT CHANGED: {display_name}")
            lines.append(f"  entries: {len(original_group)} -> {len(current_group)}")
            lines.append("")

        original_entry = original_group[-1]
        current_entry = current_group[-1]
        original_summary = entry_summary(original_entry)
        current_summary = entry_summary(current_entry)
        fields = [field for field in sorted(set(original_summary) | set(current_summary)) if field != "name" and original_summary.get(field) != current_summary.get(field)]
        if fields:
            changed_any = True
            lines.append(f"CHANGED: {display_name}")
            lines.append(f"  source: {short_source(original_entry.source_path)} -> {short_source(current_entry.source_path)}")
            for field in fields:
                lines.append(f"  {field}: {format_summary_value(original_summary.get(field))} -> {format_summary_value(current_summary.get(field))}")
            lines.append("")

    if not changed_any:
        lines.append("No changes detected.")

    return "\n".join(lines).rstrip() + "\n"


def event_entry_summary(entry: EventEntry) -> dict[str, object]:
    summary: dict[str, object] = {"name": entry.name}
    for field in EVENT_REPORT_FIELDS:
        if field in EVENT_FLAG_FIELDS:
            summary[field] = entry.flag_value(field)
        elif field == "children":
            summary[field] = tuple(tuple(sorted(child.attrib.items())) for child in entry.element.findall("./children/child"))
        else:
            summary[field] = entry.child_text(field)
    return summary


def create_event_change_report(original_entries: Iterable[EventEntry], current_entries: Iterable[EventEntry]) -> str:
    original_groups = group_entries_by_name(original_entries)
    current_groups = group_entries_by_name(current_entries)
    lines = ["RaG Economy Manager event change report", ""]
    changed_any = False

    for key in sorted(set(original_groups) | set(current_groups), key=str.casefold):
        original_group = original_groups.get(key, [])
        current_group = current_groups.get(key, [])
        display_name = (current_group or original_group)[0].name

        if not original_group:
            changed_any = True
            lines.append(f"ADDED: {display_name}")
            for entry in current_group:
                lines.append(f"  + {short_source(entry.source_path)}")
            lines.append("")
            continue

        if not current_group:
            changed_any = True
            lines.append(f"REMOVED: {display_name}")
            for entry in original_group:
                lines.append(f"  - {short_source(entry.source_path)}")
            lines.append("")
            continue

        if len(original_group) != len(current_group):
            changed_any = True
            lines.append(f"COUNT CHANGED: {display_name}")
            lines.append(f"  entries: {len(original_group)} -> {len(current_group)}")
            lines.append("")

        original_entry = original_group[-1]
        current_entry = current_group[-1]
        original_summary = event_entry_summary(original_entry)
        current_summary = event_entry_summary(current_entry)
        fields = [field for field in EVENT_REPORT_FIELDS if original_summary.get(field) != current_summary.get(field)]
        if fields:
            changed_any = True
            lines.append(f"CHANGED: {display_name}")
            lines.append(f"  source: {short_source(original_entry.source_path)} -> {short_source(current_entry.source_path)}")
            for field in fields:
                lines.append(f"  {field}: {format_summary_value(original_summary.get(field))} -> {format_summary_value(current_summary.get(field))}")
            lines.append("")

    if not changed_any:
        lines.append("No changes detected.")

    return "\n".join(lines).rstrip() + "\n"


def create_spawnable_type_change_report(original_entries: Iterable[SpawnableTypeEntry], current_entries: Iterable[SpawnableTypeEntry]) -> str:
    original_groups = group_entries_by_name(original_entries)
    current_groups = group_entries_by_name(current_entries)
    lines = ["RaG Economy Manager spawnable types change report", ""]
    changed_any = False

    for key in sorted(set(original_groups) | set(current_groups), key=str.casefold):
        original_group = original_groups.get(key, [])
        current_group = current_groups.get(key, [])
        display_name = (current_group or original_group)[0].name

        if not original_group:
            changed_any = True
            lines.append(f"ADDED: {display_name}")
            for entry in current_group:
                lines.append(f"  + {short_source(entry.source_path)}")
            lines.append("")
            continue

        if not current_group:
            changed_any = True
            lines.append(f"REMOVED: {display_name}")
            for entry in original_group:
                lines.append(f"  - {short_source(entry.source_path)}")
            lines.append("")
            continue

        if len(original_group) != len(current_group):
            changed_any = True
            lines.append(f"COUNT CHANGED: {display_name}")
            lines.append(f"  entries: {len(original_group)} -> {len(current_group)}")
            lines.append("")

        original_entry = original_group[-1]
        current_entry = current_group[-1]
        if entry_signature(original_entry) != entry_signature(current_entry):
            changed_any = True
            lines.append(f"CHANGED: {display_name}")
            lines.append(f"  source: {short_source(original_entry.source_path)} -> {short_source(current_entry.source_path)}")
            lines.append("  xml: changed")
            lines.append("")

    if not changed_any:
        lines.append("No changes detected.")

    return "\n".join(lines).rstrip() + "\n"


def create_random_preset_change_report(original_entries: Iterable[RandomPresetEntry], current_entries: Iterable[RandomPresetEntry]) -> str:
    original_groups = group_entries_by_name(original_entries)
    current_groups = group_entries_by_name(current_entries)
    lines = ["RaG Economy Manager random presets change report", ""]
    changed_any = False

    for key in sorted(set(original_groups) | set(current_groups), key=str.casefold):
        original_group = original_groups.get(key, [])
        current_group = current_groups.get(key, [])
        display_name = (current_group or original_group)[0].name

        if not original_group:
            changed_any = True
            lines.append(f"ADDED: {display_name}")
            for entry in current_group:
                lines.append(f"  + {short_source(entry.source_path)}")
            lines.append("")
            continue

        if not current_group:
            changed_any = True
            lines.append(f"REMOVED: {display_name}")
            for entry in original_group:
                lines.append(f"  - {short_source(entry.source_path)}")
            lines.append("")
            continue

        if len(original_group) != len(current_group):
            changed_any = True
            lines.append(f"COUNT CHANGED: {display_name}")
            lines.append(f"  entries: {len(original_group)} -> {len(current_group)}")
            lines.append("")

        original_entry = original_group[-1]
        current_entry = current_group[-1]
        if entry_signature(original_entry) != entry_signature(current_entry):
            changed_any = True
            lines.append(f"CHANGED: {display_name}")
            lines.append(f"  source: {short_source(original_entry.source_path)} -> {short_source(current_entry.source_path)}")
            lines.append("  xml: changed")
            lines.append("")

    if not changed_any:
        lines.append("No changes detected.")

    return "\n".join(lines).rstrip() + "\n"


def territory_zone_summary(zone: TerritoryZone) -> dict[str, str]:
    return {field: zone.attributes.get(field, "") for field in TERRITORY_REPORT_FIELDS}


def create_territory_change_report(original_zones: Iterable[TerritoryZone], current_zones: Iterable[TerritoryZone]) -> str:
    original_groups = group_entries_by_name(original_zones)
    current_groups = group_entries_by_name(current_zones)
    lines = ["RaG Economy Manager territory change report", ""]
    changed_any = False

    for key in sorted(set(original_groups) | set(current_groups), key=str.casefold):
        original_group = original_groups.get(key, [])
        current_group = current_groups.get(key, [])
        display_name = (current_group or original_group)[0].name

        if not original_group:
            changed_any = True
            lines.append(f"ADDED: {display_name}")
            for zone in current_group:
                lines.append(f"  + {short_source(zone.source_path)}")
            lines.append("")
            continue

        if not current_group:
            changed_any = True
            lines.append(f"REMOVED: {display_name}")
            for zone in original_group:
                lines.append(f"  - {short_source(zone.source_path)}")
            lines.append("")
            continue

        if len(original_group) != len(current_group):
            changed_any = True
            lines.append(f"COUNT CHANGED: {display_name}")
            lines.append(f"  entries: {len(original_group)} -> {len(current_group)}")
            lines.append("")

        original_zone = original_group[-1]
        current_zone = current_group[-1]
        original_summary = territory_zone_summary(original_zone)
        current_summary = territory_zone_summary(current_zone)
        fields = [field for field in TERRITORY_REPORT_FIELDS if original_summary.get(field) != current_summary.get(field)]
        if fields:
            changed_any = True
            lines.append(f"CHANGED: {display_name}")
            lines.append(f"  source: {short_source(original_zone.source_path)} -> {short_source(current_zone.source_path)}")
            for field in fields:
                lines.append(f"  {field}: {format_summary_value(original_summary.get(field))} -> {format_summary_value(current_summary.get(field))}")
            lines.append("")

    if not changed_any:
        lines.append("No changes detected.")

    return "\n".join(lines).rstrip() + "\n"


def write_change_report(original_entries: Iterable[TypeEntry], current_entries: Iterable[TypeEntry], output_path: str | os.PathLike[str]) -> None:
    ensure_not_ignored_storage_path(output_path)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(create_change_report(original_entries, current_entries), encoding="utf-8")


def group_entries_by_name(entries: Iterable[TypeEntry]) -> dict[str, list[TypeEntry]]:
    groups: dict[str, list[TypeEntry]] = {}
    for entry in entries:
        groups.setdefault(entry.name.casefold(), []).append(entry)
    return groups


def entry_descriptor(entry: TypeEntry) -> str:
    category = ", ".join(entry.relation_names("category")) or "-"
    return (
        f"{entry.name} "
        f"[source={short_source(entry.source_path)}, category={category}, "
        f"nominal={entry.child_text('nominal', '-') or '-'}, min={entry.child_text('min', '-') or '-'}, "
        f"lifetime={entry.child_text('lifetime', '-') or '-'}, restock={entry.child_text('restock', '-') or '-'}]"
    )


def format_summary_value(value) -> str:
    if value is None or value == "":
        return "-"
    if isinstance(value, tuple):
        if not value:
            return "-"
        if all(isinstance(item, tuple) and item and isinstance(item[0], tuple) for item in value):
            return "; ".join(", ".join(f"{key}={item_value}" for key, item_value in flag_group) for flag_group in value)
        return ", ".join(str(item) for item in value) or "-"
    return str(value)


def unique_by_name(entries: Iterable[TypeEntry]) -> dict[str, TypeEntry]:
    result: dict[str, TypeEntry] = {}
    for entry in entries:
        result[entry.name.casefold()] = entry
    return result


def apply_bulk_edit(
    entries: Iterable[TypeEntry],
    values: dict[str, str],
    category_filter: str = "",
    name_filter: str = "",
) -> int:
    count = 0
    category_filter = category_filter.strip().casefold()
    name_filter = name_filter.strip().casefold()

    for entry in entries:
        if category_filter and category_filter not in ",".join(entry.relation_names("category")).casefold():
            continue
        if name_filter and name_filter not in entry.name.casefold():
            continue
        changed = False
        for field in BULK_FIELDS:
            value = values.get(field, "").strip()
            if value == "":
                continue
            int(value)
            entry.set_child_text(field, value)
            changed = True
        if changed:
            count += 1
    return count


def multiply_numeric_field(entries: Iterable[TypeEntry], field: str, multiplier: float, category_filter: str = "", name_filter: str = "") -> int:
    if field not in BULK_FIELDS:
        raise ValueError(f"Unsupported field: {field}")

    count = 0
    category_filter = category_filter.strip().casefold()
    name_filter = name_filter.strip().casefold()
    for entry in entries:
        if category_filter and category_filter not in ",".join(entry.relation_names("category")).casefold():
            continue
        if name_filter and name_filter not in entry.name.casefold():
            continue
        value = parse_int(entry.child_text(field))
        if value is None:
            continue
        entry.set_child_text(field, str(max(0, round(value * multiplier))))
        count += 1
    return count


def replace_text_in_entries(
    entries: Iterable[TypeEntry],
    find_text: str,
    replacement_text: str,
    category_filter: str = "",
    name_filter: str = "",
) -> int:
    find_text = find_text.strip()
    if not find_text:
        return 0

    count = 0
    category_filter = category_filter.strip().casefold()
    name_filter = name_filter.strip().casefold()

    for entry in entries:
        if category_filter and category_filter not in ",".join(entry.relation_names("category")).casefold():
            continue
        if name_filter and name_filter not in entry.name.casefold():
            continue
        current_xml = format_entry_xml(entry)
        if find_text not in current_xml:
            continue

        updated = parse_type_entry_xml(current_xml.replace(find_text, replacement_text), entry.source_path, entry.source_index)
        entry.name = updated.name
        entry.element = updated.element
        count += 1

    return count


def resolve_duplicate_by_index(entries: list[TypeEntry], keep_index: int) -> int:
    if keep_index < 0 or keep_index >= len(entries):
        raise IndexError("keep_index is out of range")

    keep_entry = entries[keep_index]
    keep_name = keep_entry.name.casefold()
    removed = 0
    resolved: list[TypeEntry] = []

    for index, entry in enumerate(entries):
        if entry.name.casefold() == keep_name and index != keep_index:
            removed += 1
            continue
        resolved.append(entry)

    entries[:] = resolved
    return removed


def resolve_duplicates_by_indices(entries: list[TypeEntry], keep_indices: Iterable[int]) -> int:
    keep_by_name: dict[str, int] = {}

    for keep_index in keep_indices:
        if keep_index < 0 or keep_index >= len(entries):
            raise IndexError("keep_index is out of range")
        key = entries[keep_index].name.casefold()
        if key in keep_by_name:
            raise ValueError(f"Multiple kept entries selected for {entries[keep_index].name}")
        keep_by_name[key] = keep_index

    removed = 0
    resolved: list[TypeEntry] = []
    for index, entry in enumerate(entries):
        keep_index = keep_by_name.get(entry.name.casefold())
        if keep_index is not None and index != keep_index:
            removed += 1
            continue
        resolved.append(entry)

    entries[:] = resolved
    return removed


def merged_entries(entries: Iterable[TypeEntry], keep: str = "last") -> list[TypeEntry]:
    if keep not in {"first", "last"}:
        raise ValueError("keep must be 'first' or 'last'")

    result: dict[str, TypeEntry] = {}
    order: list[str] = []
    for entry in entries:
        key = entry.name.casefold()
        if key not in result:
            order.append(key)
            result[key] = entry.clone()
        elif keep == "last":
            result[key] = entry.clone()
    return [result[key] for key in order]


def safe_types_split_token(value: str, fallback: str = "uncategorized") -> str:
    token = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip()).strip("._-")
    return token or fallback


def display_types_split_label(value: str, fallback: str = "Uncategorized") -> str:
    token = safe_types_split_token(value, fallback)
    return token[:1].upper() + token[1:] if token else fallback


def type_entry_category_key(entry: TypeEntry) -> str:
    return safe_types_split_token(entry.first_relation_name("category"), "uncategorized").casefold()


def classname_family_label(name: str) -> str:
    name = str(name or "").strip()
    if not name:
        return "unnamed"

    separator_parts = [part for part in re.split(r"[_\-.]+", name) if part]
    if len(separator_parts) > 1 and separator_parts[0].isupper() and len(separator_parts[0]) <= 4:
        return safe_types_split_token(separator_parts[0], "unnamed")

    tokens: list[str] = []
    for part in separator_parts or [name]:
        part_tokens = re.findall(r"[A-Z]+(?=[A-Z][a-z]|$)|[A-Z]?[a-z]+|\d+", part)
        tokens.extend(part_tokens or [part])

    if len(tokens) >= 2:
        if str(tokens[1]).isdigit():
            return safe_types_split_token(tokens[0], "unnamed")
        if separator_parts and "_" in name and separator_parts[0].islower():
            return safe_types_split_token("_".join(tokens[:2]), "unnamed")
        return safe_types_split_token("".join(tokens[:2]), "unnamed")
    return safe_types_split_token(tokens[0] if tokens else name, "unnamed")


def type_entry_prefix_key(entry: TypeEntry) -> str:
    return type_entry_prefix_label(entry).casefold()


def type_entry_prefix_label(entry: TypeEntry) -> str:
    name = str(entry.name or "").strip()
    return classname_family_label(name)


def parse_type_split_rules(raw_rules: str | Iterable[str] = "") -> list[TypeSplitRule]:
    if isinstance(raw_rules, str):
        lines = raw_rules.splitlines()
    else:
        lines = list(raw_rules or [])
    rules: list[TypeSplitRule] = []
    for raw_line in lines:
        line = str(raw_line or "").strip()
        if not line or line.startswith("#"):
            continue
        if "#" in line:
            line = line.split("#", 1)[0].strip()
        match = re.match(r"^(prefix|category)\s*[:=]\s*(.+?)\s*(?:->|=>|=)\s*(.+)$", line, re.IGNORECASE)
        if not match:
            raise ValueError(f"Invalid split rule: {raw_line}")
        kind = match.group(1).strip().casefold()
        pattern = match.group(2).strip()
        label = safe_types_split_token(match.group(3).strip(), "")
        if not pattern or not label:
            raise ValueError(f"Invalid split rule: {raw_line}")
        rules.append(TypeSplitRule(kind, pattern, label))
    return rules


def normalized_classname_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(name or "").casefold())


def advanced_type_split_label_info(entry: TypeEntry, rules: Iterable[TypeSplitRule] = ()) -> tuple[str, bool]:
    name = str(entry.name or "")
    name_key = name.casefold()
    compact_key = normalized_classname_key(name)
    categories = [value.casefold() for value in entry.relation_names("category")]
    for rule in rules:
        pattern_key = rule.pattern.casefold()
        if rule.kind == "prefix" and name_key.startswith(pattern_key):
            return rule.label, False
        if rule.kind == "category" and pattern_key in categories:
            return rule.label, False

    if compact_key in {"scientificbriefcase", "scientificbriefcasekeys"}:
        return "Uncategorized", False

    vehicle_families = (
        "civiliansedan",
        "offroadhatchback",
        "hatchback02",
        "offroad02",
        "sedan02",
        "truck01",
    )
    if compact_key.startswith("boat"):
        return "Boat", False
    if compact_key.startswith(vehicle_families):
        return "Vehicles", False

    if name_key.startswith(("zmbm", "zmbf")):
        return "Infected", False
    if name_key.startswith("animal"):
        return "Animals", False
    if name_key.startswith("vehicle"):
        return "Vehicles", False
    if "lootdispatch" in categories:
        return "Vehicles", False
    if name_key.startswith(("land_", "staticobj_")):
        return "Static", False
    if name_key.endswith("pelt"):
        return "Pelts", False
    if name_key.endswith(("steakmeat", "filletmeat")):
        return "Meat", False
    if compact_key in {"watchtower", "watchtowerkit", "fence", "fencekit"}:
        return "BaseBuilding", False

    category = entry.first_relation_name("category")
    if category:
        return safe_types_split_token(category, "uncategorized"), False
    return type_entry_prefix_label(entry), True


def advanced_type_split_label(entry: TypeEntry, rules: Iterable[TypeSplitRule] = ()) -> str:
    return advanced_type_split_label_info(entry, rules)[0]


def split_type_entries(
    entries: Iterable[TypeEntry],
    strategy: str = "category",
    custom_rules: str | Iterable[str] = "",
) -> list[TypeSplitGroup]:
    strategy_key = str(strategy or "").strip().casefold()
    if strategy_key not in {"category", "prefix", "advanced"}:
        raise ValueError("strategy must be 'category', 'prefix', or 'advanced'")
    rules = parse_type_split_rules(custom_rules) if strategy_key == "advanced" else []

    groups: dict[str, list[TypeEntry]] = {}
    labels: dict[str, str] = {}
    collapse_singleton_keys: set[str] = set()
    for entry in entries:
        if strategy_key == "category":
            raw_label = entry.first_relation_name("category") or "uncategorized"
            key = type_entry_category_key(entry)
        elif strategy_key == "prefix":
            key = type_entry_prefix_key(entry)
            raw_label = type_entry_prefix_label(entry)
            collapse_singleton_keys.add(key)
        else:
            raw_label, collapse_singleton = advanced_type_split_label_info(entry, rules)
            key = safe_types_split_token(raw_label, "uncategorized").casefold()
            if collapse_singleton:
                collapse_singleton_keys.add(key)
        groups.setdefault(key, []).append(entry.clone())
        labels.setdefault(key, safe_types_split_token(raw_label, "uncategorized"))

    uncategorized_key = "uncategorized"
    for key in list(groups):
        if key == uncategorized_key or key not in collapse_singleton_keys or len(groups[key]) != 1:
            continue
        groups.setdefault(uncategorized_key, []).extend(groups.pop(key))
        labels.setdefault(uncategorized_key, "Uncategorized")
        labels.pop(key, None)

    result: list[TypeSplitGroup] = []
    for key in sorted(groups):
        label = display_types_split_label(labels[key])
        result.append(
            TypeSplitGroup(
                key=key,
                label=label,
                filename=f"{label}.xml",
                entries=tuple(sorted(groups[key], key=lambda item: item.name.casefold())),
            )
        )
    return result


def cfgeconomycore_existing_files(content: str, folder: str, file_type: str) -> set[str]:
    folder_key = str(folder or "").replace("\\", "/").strip("/").casefold()
    type_key = str(file_type or "").strip().casefold()
    if not folder_key or not type_key:
        return set()
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return set()
    existing: set[str] = set()
    for ce_element in root.findall(".//ce"):
        ce_folder = ce_element.attrib.get("folder", "").replace("\\", "/").strip("/").casefold()
        if ce_folder != folder_key:
            continue
        for file_element in ce_element.findall("file"):
            if file_element.attrib.get("type", "").strip().casefold() == type_key:
                name = file_element.attrib.get("name", "").strip()
                if name:
                    existing.add(name.casefold())
    return existing


def cfgeconomycore_existing_type_files(content: str, folder: str) -> set[str]:
    return cfgeconomycore_existing_files(content, folder, "types")


def update_cfgeconomycore_file_refs_text(content: str, folder: str, filenames: Iterable[str], file_type: str) -> tuple[str, list[str]]:
    folder = str(folder or "").replace("\\", "/").strip("/")
    if not folder:
        raise ValueError("folder is required")
    file_type = str(file_type or "").strip()
    if not file_type:
        raise ValueError("file_type is required")
    safe_filenames = [safe_types_split_token(Path(str(filename)).name, "types.xml") for filename in filenames]
    existing = cfgeconomycore_existing_files(content, folder, file_type)
    missing = [filename for filename in safe_filenames if filename.casefold() not in existing]
    if not missing:
        return content, []

    if "<economycore" in content and "</economycore>" not in content:
        self_closing = re.search(r"<economycore\b([^>]*)/>", content, re.IGNORECASE)
        if self_closing:
            attrs = self_closing.group(1).rstrip()
            content = (
                content[: self_closing.start()]
                + f"<economycore{attrs}>\n</economycore>"
                + content[self_closing.end() :]
            )

    if "<economycore" not in content or "</economycore>" not in content:
        raise ValueError("cfgeconomycore.xml must contain an <economycore> root")

    escaped_folder = re.escape(folder)
    ce_self_closing_pattern = re.compile(
        rf"<ce\b(?=[^>]*\bfolder\s*=\s*['\"]{escaped_folder}['\"])([^>]*)/>",
        re.IGNORECASE,
    )
    ce_self_closing = ce_self_closing_pattern.search(content)
    if ce_self_closing:
        attrs = ce_self_closing.group(1).rstrip()
        content = (
            content[: ce_self_closing.start()]
            + f"<ce{attrs}>\n</ce>"
            + content[ce_self_closing.end() :]
        )

    ce_pattern = re.compile(
        rf"<ce\b(?=[^>]*\bfolder\s*=\s*['\"]{escaped_folder}['\"])[^>]*>",
        re.IGNORECASE,
    )
    ce_match = ce_pattern.search(content)
    if ce_match:
        close_index = content.find("</ce>", ce_match.end())
        if close_index < 0:
            raise ValueError("target <ce> block is missing its closing </ce>")
        line_start = content.rfind("\n", 0, close_index) + 1
        close_indent = re.match(r"[ \t]*", content[line_start:close_index]).group(0)
        file_indent = close_indent + "    "
        insert_text = "".join(
            f'{file_indent}<file name="{escape(filename)}" type="{escape(file_type)}" />\n'
            for filename in missing
        )
        return content[:close_index] + insert_text + content[close_index:], missing

    root_close_index = content.rfind("</economycore>")
    line_start = content.rfind("\n", 0, root_close_index) + 1
    root_indent = re.match(r"[ \t]*", content[line_start:root_close_index]).group(0)
    child_indent = root_indent + "    "
    block = [f'{root_indent}<ce folder="{escape(folder)}">\n']
    block.extend(f'{child_indent}<file name="{escape(filename)}" type="{escape(file_type)}" />\n' for filename in missing)
    block.append(f"{root_indent}</ce>\n")
    return content[:root_close_index] + "".join(block) + content[root_close_index:], missing


def update_cfgeconomycore_types_text(content: str, folder: str, filenames: Iterable[str]) -> tuple[str, list[str]]:
    return update_cfgeconomycore_file_refs_text(content, folder, filenames, "types")


def write_types_file(entries: Iterable[TypeEntry], output_path: str | os.PathLike[str], keep_duplicates: bool = False) -> None:
    ensure_not_ignored_storage_path(output_path)
    entries = list(entries if keep_duplicates else merged_entries(entries, keep="last"))
    text = render_types_xml(entries, keep_duplicates=True)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_spawnable_types_file(entries: Iterable[SpawnableTypeEntry], output_path: str | os.PathLike[str]) -> None:
    ensure_not_ignored_storage_path(output_path)
    root = ET.Element("spawnabletypes")
    for entry in sorted(entries, key=lambda item: item.name.casefold()):
        root.append(copy.deepcopy(entry.element))

    indent_xml(root)
    tree = ET.ElementTree(root)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    tree.write(output_path, encoding="utf-8", xml_declaration=True)


def write_random_presets_file(entries: Iterable[RandomPresetEntry], output_path: str | os.PathLike[str]) -> None:
    ensure_not_ignored_storage_path(output_path)
    root = ET.Element("randompresets")
    for entry in sorted(entries, key=lambda item: (item.kind.casefold(), item.name.casefold())):
        root.append(copy.deepcopy(entry.element))

    indent_xml(root)
    tree = ET.ElementTree(root)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    tree.write(output_path, encoding="utf-8", xml_declaration=True)


def render_types_xml(entries: Iterable[TypeEntry], keep_duplicates: bool = False) -> str:
    entries = list(entries if keep_duplicates else merged_entries(entries, keep="last"))
    root = ET.Element("types")
    for entry in sorted(entries, key=lambda item: item.name.casefold()):
        element = copy.deepcopy(entry.element)
        order_type_entry_children(element)
        root.append(element)

    indent_xml(root)
    body = ET.tostring(root, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' + body


def write_events_file(entries: Iterable[EventEntry], output_path: str | os.PathLike[str]) -> None:
    ensure_not_ignored_storage_path(output_path)
    output_path = Path(output_path)
    if output_path.is_file():
        parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
        root = parse_xml_file(output_path, parser=parser).getroot()
        if root.tag != "events":
            raise ValueError(f"Expected root <events>, got <{root.tag}>.")
    else:
        root = ET.Element("events")
    event_elements = []
    for entry in entries:
        element = copy.deepcopy(entry.element)
        order_event_entry_children(element)
        event_elements.append(element)

    _reconcile_xml_children(root, "event", event_elements)
    indent_xml(root)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(output_path, encoding="utf-8", xml_declaration=True)


def _reconcile_xml_children(parent: ET.Element, tag: str, replacements: Iterable[ET.Element]) -> None:
    replacement_list = [copy.deepcopy(element) for element in replacements]
    replacement_index = 0
    for child in list(parent):
        if child.tag != tag:
            continue
        child_index = list(parent).index(child)
        parent.remove(child)
        if replacement_index < len(replacement_list):
            parent.insert(child_index, replacement_list[replacement_index])
            replacement_index += 1
    for element in replacement_list[replacement_index:]:
        parent.append(element)


def event_spawn_group_to_element(group: EventSpawnGroup) -> ET.Element:
    element = copy.deepcopy(group.element)
    element.tag = "event"
    element.attrib["name"] = group.name
    zones = [child for child in list(element) if child.tag == "zone"]
    if group.zone is None:
        for zone in zones:
            element.remove(zone)
    else:
        zone_element = copy.deepcopy(group.zone.element)
        zone_element.attrib.clear()
        zone_element.attrib.update(group.zone.attributes)
        if zones:
            zone_index = list(element).index(zones[0])
            element.remove(zones[0])
            element.insert(zone_index, zone_element)
        else:
            first_pos = next((index for index, child in enumerate(list(element)) if child.tag == "pos"), len(element))
            element.insert(first_pos, zone_element)
    position_elements = []
    for position in group.positions:
        position_element = copy.deepcopy(position.element) if position.element is not None else ET.Element("pos")
        position_element.tag = "pos"
        position_element.attrib.clear()
        position_element.attrib.update(position.attributes)
        position_elements.append(position_element)
    _reconcile_xml_children(element, "pos", position_elements)
    return element


def event_group_definition_to_element(group: EventGroupDefinition) -> ET.Element:
    element = copy.deepcopy(group.element)
    element.tag = "group"
    element.attrib["name"] = group.name
    child_elements = []
    for child in group.children:
        child_element = copy.deepcopy(child.element)
        child_element.tag = "child"
        child_element.attrib.clear()
        child_element.attrib.update(child.attributes)
        child_elements.append(child_element)
    _reconcile_xml_children(element, "child", child_elements)
    return element


def format_event_spawn_group_xml(group: EventSpawnGroup) -> str:
    return xml_element_to_text(event_spawn_group_to_element(group))


def format_event_group_definition_xml(group: EventGroupDefinition) -> str:
    return xml_element_to_text(event_group_definition_to_element(group))


def write_event_spawns_file(groups: Iterable[EventSpawnGroup], output_path: str | os.PathLike[str]) -> None:
    ensure_not_ignored_storage_path(output_path)
    output_path = Path(output_path)
    if output_path.is_file():
        parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
        root = parse_xml_file(output_path, parser=parser).getroot()
        if root.tag != "eventposdef":
            raise ValueError(f"Expected root <eventposdef>, got <{root.tag}>.")
    else:
        root = ET.Element("eventposdef")
    _reconcile_xml_children(root, "event", (event_spawn_group_to_element(group) for group in groups))
    ET.indent(root, space="    ")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(output_path, encoding="utf-8", xml_declaration=True)


def write_event_groups_file(groups: Iterable[EventGroupDefinition], output_path: str | os.PathLike[str]) -> None:
    ensure_not_ignored_storage_path(output_path)
    output_path = Path(output_path)
    if output_path.is_file():
        parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
        root = parse_xml_file(output_path, parser=parser).getroot()
        if root.tag != "eventgroupdef":
            raise ValueError(f"Expected root <eventgroupdef>, got <{root.tag}>.")
    else:
        root = ET.Element("eventgroupdef")
    _reconcile_xml_children(root, "group", (event_group_definition_to_element(group) for group in groups))
    ET.indent(root, space="    ")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(output_path, encoding="utf-8", xml_declaration=True)


def write_territory_file(zones: Iterable[TerritoryZone], output_path: str | os.PathLike[str], groups: Iterable[TerritoryGroup] | None = None) -> None:
    ensure_not_ignored_storage_path(output_path)
    zones = list(zones)
    groups = list(groups or [])
    output_path = Path(output_path)
    root_attributes: dict[str, str] = {}
    try:
        tree = parse_xml_file(output_path)
        root = tree.getroot()
        root_attributes = {str(key): str(value) for key, value in root.attrib.items()}
        existing_zones = root.findall(".//zone")
        if not groups and root.tag == "territory-type" and len(existing_zones) == len(zones):
            for element, zone in zip(existing_zones, zones):
                element.attrib.clear()
                element.attrib.update(zone.element.attrib)
            indent_xml(root)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            tree.write(output_path, encoding="utf-8", xml_declaration=True)
            return
    except (OSError, ET.ParseError):
        pass

    root = ET.Element("territory-type", root_attributes)
    grouped: dict[int, dict[str, object]] = {}
    group_order: list[int] = []
    for group_meta in sorted(groups, key=lambda item: item.group_index):
        group_index = int(group_meta.group_index or 0)
        if group_index not in grouped:
            group_order.append(group_index)
            grouped[group_index] = {
                "attributes": dict(group_meta.attributes or {}),
                "zones": [],
            }
    for zone in zones:
        group_index = int(getattr(zone, "group_index", 0) or 0)
        if group_index not in grouped:
            group_order.append(group_index)
            grouped[group_index] = {
                "attributes": dict(getattr(zone, "group_attributes", {}) or {}),
                "zones": [],
            }
        grouped[group_index]["zones"].append(zone)

    for group_index in group_order:
        group_data = grouped[group_index]
        group = ET.Element("territory", group_data["attributes"])
        for zone in group_data["zones"]:
            group.append(copy.deepcopy(zone.element))
        root.append(group)
    indent_xml(root)
    tree = ET.ElementTree(root)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tree.write(output_path, encoding="utf-8", xml_declaration=True)


def format_entry_xml(entry: TypeEntry) -> str:
    element = copy.deepcopy(entry.element)
    order_type_entry_children(element)
    indent_xml(element)
    return ET.tostring(element, encoding="unicode")


def format_event_xml(entry: EventEntry) -> str:
    element = copy.deepcopy(entry.element)
    order_event_entry_children(element)
    indent_xml(element)
    return ET.tostring(element, encoding="unicode")


def format_spawnable_type_xml(entry: SpawnableTypeEntry) -> str:
    element = copy.deepcopy(entry.element)
    indent_xml(element)
    return ET.tostring(element, encoding="unicode")


def format_random_preset_xml(entry: RandomPresetEntry) -> str:
    element = copy.deepcopy(entry.element)
    indent_xml(element)
    return ET.tostring(element, encoding="unicode")


def format_territory_xml(zone: TerritoryZone) -> str:
    element = copy.deepcopy(zone.element)
    indent_xml(element)
    return ET.tostring(element, encoding="unicode")


def indent_xml(element: ET.Element, level: int = 0) -> None:
    indent = "\n" + level * "    "
    child_indent = "\n" + (level + 1) * "    "
    children = list(element)
    if children:
        if not element.text or not element.text.strip():
            element.text = child_indent
        for child in children:
            indent_xml(child, level + 1)
        if not children[-1].tail or not children[-1].tail.strip():
            children[-1].tail = indent
    if level and (not element.tail or not element.tail.strip()):
        element.tail = indent
