"""R2X utils functions."""

# ruff: noqa
import io
import json
import ast
from operator import attrgetter
import functools

# Standard packages
import os
import shutil
from collections.abc import Iterable
from collections import ChainMap
import difflib
from importlib.resources import files
from pathlib import Path
from itertools import islice

# Third-party packages
import numpy as np
import pandas as pd
import polars as pl
import yaml
from jsonschema import validate
from loguru import logger
import pint
from pint import UndefinedUnitError
from infrasys.base_quantity import BaseQuantity
from r2x.units import ureg


DEFAULT_OUTPUT_FOLDER: str = "r2x_export"
DEFAULT_DATA_FOLDER: str = "data"
DEFAULT_PLUGIN_PATH: str = "r2x.plugins"
DEFAULT_SEARCH_FOLDERS = [
    "outputs",
    "inputs_case",
    "outputs_perturb",
    "inputs_case/supplycurve_metadata",
    ".",
]


def get_project_root() -> Path:
    """Return the project absolute path.

    Args:
        as_str: Return the Path as string
    """
    return Path(__file__).parent.parent


def validate_string(value):
    """Read cases flag value and convert it to Python type."""
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    if value == "true" or value == "TRUE":
        return True
    if value == "false" or value == "FALSE":
        return False
    # value = ast.literal_eval(value)
    # if len(value.split(",")) > 1:
    #     return value.split(",")
    try:
        value = ast.literal_eval(value)
    except:
        logger.trace("Could not parse {}", value)
    finally:
        return value


def get_max_value(
    df: pd.DataFrame,
    column: str,
    aggregator: list | str = "region",
) -> dict[str, float]:
    """Return max value of dataframe as a dict for a given aggregator.

    Args:
        df: Data to aggregate,
        column: Column to get,
        aggregator: Column to aggregate.
    """
    return df.max().to_dict()
    # return df.groupby(aggregator, observed=True)[column].max().to_dict()


def get_mean_data(
    data: pd.DataFrame,
    column: str,
    new_name: str,
    rename_dict: dict = {},
    categories=["tech", "tech_class", "region"],
) -> pd.DataFrame:
    """Return the mean value for a given dataframe and given categories.

    Args:
        data: Dataframe to aggregate,
        column: Column that will be renamed,
        new_name: New name of column,
        rename_dict: Dictionary passed to pd.DataFrame().rename(columns=rename_dict),
        categories: Keys to aggregate the data.

    Returns
    -------
        Aggregated dataframe
    """
    if not rename_dict:
        rename_dict = {column: new_name}

    averaged_data = data.rename(columns=rename_dict).groupby(categories).agg(np.mean).reset_index()
    return averaged_data


def override_dict(base_dict: dict, override_dict: ChainMap | dict | None = None) -> dict:
    """Update a base dictionary with values from an override dictionary.

    Parameters
    ----------
    base_dict : dict
        The base dictionary to be updated.
    override_dict : ChainMap | dict | None, optional
        A dictionary containing override values. If None, returns base_dict unchanged.

    Returns
    -------
    dict
        The updated dictionary.

    Examples
    --------
    1. Simple update with a key-value pair:

    >>> base_dict = {"a": 1, "b": 2}
    >>> override_dict = {"b": 3}
    >>> update_dict(base_dict, override_dict)
    {'a': 1, 'b': 3}

    2. Merging nested dictionaries:

    >>> base_dict = {"a": 1, "b": {"x": 10}}
    >>> override_dict = {"b": {"y": 20}}
    >>> update_dict(base_dict, override_dict)
    {'a': 1, 'b': {'x': 10, 'y': 20}}

    3. Full replacement of a nested dictionary:

    >>> base_dict = {"a": 1, "b": {"x": 10}}
    >>> override_dict = {"b": {"_replace": True, "y": 20}}
    >>> update_dict(base_dict, override_dict)
    {'b': {'y': 20}}

    4. Adding new keys:

    >>> base_dict = {"a": 1}
    >>> override_dict = {"b": 2}
    >>> update_dict(base_dict, override_dict)
    {'a': 1, 'b': 2}

    5. Replacing the entire dictionary:

    >>> base_dict = {"a": 1, "b": 2}
    >>> override_dict = {"_replace": True, "c": 3}
    >>> update_dict(base_dict, override_dict)
    {'c': 3}

    6. No override (when override_dict is None):

    >>> base_dict = {"a": 1}
    >>> override_dict = None
    >>> update_dict(base_dict, override_dict)
    {'a': 1}
    """
    if not override_dict:
        return base_dict

    def recursive_update(base, overrides):
        for key, value in overrides.items():
            if isinstance(value, dict):
                if "_replace" in value:
                    base[key] = value.copy()
                    base[key].pop("_replace")
                elif key not in base:
                    base[key] = value
                elif isinstance(base[key], dict) and isinstance(value, dict):
                    recursive_update(base[key], value)
                else:
                    base[key] = value
            else:
                base[key] = value

    recursive_update(base_dict, override_dict)
    return base_dict


def read_user_dict(fname: str) -> dict:
    """Load Yaml to Python dictionary."""
    if fname.strip().startswith("["):
        msg = "JSON arrays not supported for user dict."
        raise ValueError(msg)

    if fname.strip().startswith("{"):
        try:
            return json.loads(fname)  # Parse the JSON string directly
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON string provided: {e}")

    _, ext = os.path.splitext(fname)
    ext = ext.lower()
    match ext:
        case ".json":
            return _load_file(fname, json.load)  # Load JSON
        case ".yaml" | ".yml":
            return _load_file(fname, yaml.safe_load)  # Load YAML
        case _:
            raise ValueError(f"Unsupported file extension: {ext}. Only .json, .yaml, and .yml are supported.")


def _load_file(fname: str, loader) -> dict:
    """Helper function to load a file (either JSON or YAML)."""
    try:
        with open(fname) as f:
            return loader(f)
    except FileNotFoundError:
        raise FileNotFoundError(f"File {fname} not found.")
    except OSError as e:
        raise OSError(f"Error reading the file {fname}: {e}")


def read_json(fname: str):
    """Load JSON file."""
    fpath = str(get_project_root())

    # Read configuration file
    with open(os.path.join(fpath, fname)) as f:
        return json.load(f)


def read_fmap(fname: str):
    """Read default fmap mapping for ReEDS files."""
    fmap = read_json(fname)
    validate(instance=fmap, schema=mapping_schema)

    # Lowercase dictionary
    fmap = {key.lower() if isinstance(key, str) else key: value for key, value in fmap.items()}
    return fmap


def get_missing_columns(fpath: str, column_names: list) -> list:
    """List of missing coluns from a csv file.

    We just read the first row of a CSV to check the name of the columns

    Args:
        fpath: Path to the csv file
        column_names: list of columns to verify

    Returns
    -------
        A list of missing columns or empty list
    """
    try:
        _ = pd.read_csv(fpath, nrows=0).rename(columns=DEFAULT_COLUMN_MAP)
    except pd.errors.EmptyDataError:
        logger.error(f"Required file for R2X:{fpath} is empty!")
        raise

    return [col for col in column_names if col not in _.columns.str.lower()]


def get_missing_files(project_folder: str, file_list: Iterable, max_depth: int = 2) -> list:
    """List of missing required files from project folder.

    This function looks recursively in the project folder. For safety we only
    look 2 levels of folders

    Args:
        project_folder: Folder to look for the files
        file_list: Iterable of files to check
        max_depth: Level of subfolders to look.

    Returns
    -------
        A list with the missing files or empty list
    """
    all_files = set()

    # Initialize stack with the project folder and depth 0
    stack: list[tuple[str, int]] = [(project_folder, 0)]

    while stack:
        current_folder, current_depth = stack.pop()

        if current_depth > max_depth:
            continue

        for root, dirs, dir_files in os.walk(current_folder):
            for file_name in dir_files:
                file_path = os.path.join(root, file_name)
                all_files.add(os.path.basename(file_path))

            for folder in dirs:
                next_folder = os.path.join(root, folder)
                stack.append((next_folder, current_depth + 1))
    missing_files = [f for f in file_list if os.path.basename(f) not in all_files]
    return missing_files


def read_csv(fname: str, package_data: str = "r2x.defaults", **kwargs) -> pl.LazyFrame:
    """Helper function to read csv string data from package data.

    Args:
        fname: Name of the csv file
        package_data: Location of file in package. Default location is r2x.defaults
        **kwargs: Additional keys passed to pandas read_csv function

    Returns
    -------
        A pandas dataframe of the csv requested
    """
    csv_file = files(package_data).joinpath(fname).read_text(encoding="utf-8-sig")
    return pl.LazyFrame(pl.read_csv(io.StringIO(csv_file), **kwargs))


def get_timeindex(
    start_year: int = 2007, end_year: int = 2013, tz: str = "EST", year: int | None = None
) -> pd.DatetimeIndex:
    """ReEDS time indices are in EST, and leap years drop Dec 31 instead of Feb 29.

    Notes
    -----
        - Function courtesy of P. Brown.

    Args:
        start_year: First year of data
        end_year: Last year of data
        tz: Time zone to convert to
        year: Create a 8760 profile for the given year
    Returns:
        Time index as numpy.ndarray
    """
    if year is not None:
        return pd.date_range(
            f"{year}-01-01",
            f"{year + 1}-01-01",
            freq="H",
            inclusive="left",
            tz=tz,
        )[
            :8760  # This will remove extra day from leap years
        ]

    time_index = np.ravel(
        [
            pd.date_range(
                f"{y}-01-01",
                f"{y + 1}-01-01",
                freq="H",
                inclusive="left",
                tz=tz,
            )[
                :8760  # This will remove extra day from leap years
            ]
            for y in range(start_year, end_year + 1)
        ]
    )
    return pd.to_datetime(time_index)


def clean_folder(path) -> None:
    """Create new folder if doesnt not exist. Purge if it does."""
    if os.path.exists(path):
        shutil.rmtree(path)
        os.makedirs(path)
    else:
        os.makedirs(path)
    return


def check_file_exists(
    fname: str,
    run_folder: str | os.PathLike,
    optional: bool = False,
) -> os.PathLike | None:
    """Return file path for a given filename if exists in folders.

    If it does not exists and is mandatory, it raises an FileNotFoundError

    Args:
        fname: Filename with extension of the desired file
        run_folder: Base folder of the run
        folders: Root folder name to start looking. Default: run_folder
        default_folders: Default location to look for files
        mandatory: Flag to identify needed files

    Returns
    -------
        fpath or None
    """
    run_folder = Path(run_folder)
    # Set run_folder as default folder to look
    search_paths = [run_folder / folder for folder in DEFAULT_SEARCH_FOLDERS]

    file_matches = []
    for search_path in search_paths:
        if search_path.is_file():
            file_matches.append(search_path)
        for fpath in search_path.glob(fname):  # Rglob search recursively
            if fpath.name == fname:
                logger.trace("File '{}' found in {}", fname, fpath.parent)
                file_matches.append(fpath)

    if len(file_matches) > 1:
        msg = (
            f"Multiple files found for {fname}. Returning first match. "
            f"Check for copies of the files in the {file_matches}"
        )
        logger.warning(msg)

    if not optional and not file_matches:
        raise FileNotFoundError(f"Mandatory file '{fname}' not found in {run_folder}.")
    elif not file_matches:
        logger.warning(f"File: '{fname}' not found in {run_folder}.")
        return None

    match = file_matches[0]
    return match


def get_csv(fpath: str, fname: str, fmap: dict[str, str | dict | list] = {}, **kwargs) -> None | pd.DataFrame:
    """Custom CSV file reader.

    This function reads a csv file using the fpath and save it into memory
    using the fname. In addition, it applies a custom column mapping if
    passed throught fmap dictionary.

    Args:
        fpath: Location of the file
        fname: Name or alias of the requested file
        fmap: File mapping values
        kwargs: Additional key arguments for pandas mostly

    Attributes
    ----------
        data:  ReEDS parsed data for PCM.
    """
    logger.debug(f"Attempting to read {fname}")

    config = read_json("r2x/defaults/config.json")
    column_mapping = config.get("default_column_mapping")
    custom_mapping = fmap.get("column_mapping", {})
    dtype = fmap.get("dtype", {})
    df_index = fmap.get("column_index", {})
    columns = fmap.get("columns", None)
    keep_case = fmap.get("keep_case", False)

    try:
        _ = pd.read_csv(fpath, header=0, **kwargs)
    except pd.errors.EmptyDataError:
        logger.warning(f"{fpath} is empty. Skipping it.")
        return None

    # This is a second safety mechanism if the file does infact has something
    # on it but no data.
    if _.empty:
        logger.warning(f"{fpath} is empty. Skipping it.")
        return None

    # Check if there are more columns added from the upgrader. For the moment,
    # we just append the s-region for validating the merge between different
    # dataframes.
    if columns is not None and isinstance(columns, list):
        # Assume that we only add one column for the moment.
        if len(_.columns) > len(columns):
            columns.append(_.columns[-1])

    # Reading CSV file
    data = (
        pd.read_csv(fpath, names=columns, header=0, **kwargs)  # type: ignore
        .rename(columns=str.lower)
        .rename(columns=column_mapping)
        .rename(columns=custom_mapping)
        .map(lambda r: r.lower() if isinstance(r, str) and not keep_case else r)
        .astype(dtype)
    )

    # Columns instead of rows. We dont know if in the future they are going to
    # change the format.  But just to be consistent with the other
    # variables that we read.
    match fname:
        case "years":
            data = data.T

    # Add index if passed.
    if df_index:
        try:
            data = data.set_index(df_index)
        except KeyError:
            raise KeyError(f"{df_index} not in {fname}.")

    # Create entry point to table
    logger.debug(f"Loaded file {fname}")
    return data


# NOTE: This is not actually used in the process but is kept here for documentation
# purposes.
def create_nodal_database(
    plexos_node_to_reeds_region_fpath: str,
    node_objects_fpath: str,
    reeds_shapefiles_fpath: str,
) -> pd.DataFrame:
    """Un-used function, but creates the nodes objects for the Nodal dataset."""
    import geopandas as gpd  # type: ignore

    nodes_to_ba = pd.read_csv(plexos_node_to_reeds_region_fpath)
    nodes_to_ba["node_id"] = nodes_to_ba["node"].str.split("_", n=1).str[0]
    nodes_to_ba = nodes_to_ba.loc[nodes_to_ba["node_id"].str.isnumeric()]
    nodes_to_ba = nodes_to_ba.groupby("node_id").agg({"reeds_region": list})
    nodes_to_ba["num_bas"] = nodes_to_ba["reeds_region"].str.len()
    nodes_to_ba = nodes_to_ba.loc[nodes_to_ba["num_bas"] == 1]
    nodes_to_ba["reeds_region"] = nodes_to_ba["reeds_region"].str[0]
    nodes_to_ba = nodes_to_ba.reset_index()[["node_id", "reeds_region"]]

    node_objects = node_objects_fpath
    usecols = ["Latitude", "Longitude", "Node ID", "kV", "Node", "Load Factor"]
    rename_dict = {
        "Node ID": "node_id",
        "kV": "voltage",
        "Load Factor": "load_participation_factor",
    }
    dtype = {
        "Node ID": "str",
        "Latitude": "float64",
        "Longitude": "float64",
        "kV": "float64",
        "Load Factor": "float64",
    }
    nodes = (
        pd.read_csv(node_objects, low_memory=False, dtype=dtype, usecols=usecols)  # type: ignore
        .rename(columns=rename_dict)
        .rename(columns=str.lower)
    )

    # Convert it to geopandas dataframe to assign the ReEDs region from the shapefile
    # NOTE: This step is not necessary if the node csv has the ReEDS region.
    reeds_shp = gpd.read_file(reeds_shapefiles_fpath)
    nodes = gpd.GeoDataFrame(nodes, geometry=gpd.points_from_xy(nodes.longitude, nodes.latitude)).set_crs(
        "epsg:4326"
    )
    nodes = gpd.sjoin(nodes, reeds_shp, predicate="within")

    nodes_to_bas = nodes.merge(nodes_to_ba, on="node_id", how="outer")
    nodes_to_bas.loc[nodes_to_bas["reeds_region"].isna(), "reeds_region"] = nodes_to_bas["pca"]
    nodes_to_bas.loc[
        (nodes_to_bas["reeds_region"] != nodes_to_bas["pca"]) & (~nodes_to_bas["pca"].isna()),
        "reeds_region",
    ] = nodes_to_bas["pca"]

    node_data = nodes_to_bas[
        [
            "node_id",
            "latitude",
            "longitude",
            "reeds_region",
            "voltage",
            "load_participation_factor",
        ]
    ]
    node_data["plexos_id"] = node_data["node_id"]
    return node_data


def invert_dict(d: dict[str, list]) -> dict[str, str]:
    """Inverse dictionary with list values.

    Example:
        dict = {"a": [1,2], "b":[3,4]}
        invers = {1:"a", 2: "a", 3:"b", 4 : "b"}
    """
    if d is None:
        return {}
    return {value: key for key, inner_list in d.items() for value in inner_list}


def match_category(row, categories, cutoff=0.6):
    """Return the n_return closest match based on the cutoff.

    This is a wrapper function of the difflib.get_close_matches function,
    except it just return the first value that if inds.

    Args:
        row: String to match,
        categories: categories to match against,
        n_return: number of matches to return,
        cutoff: Cutoff of the algorithm (0-1]. 1 being perfect match.
    """
    from difflib import get_close_matches

    result = get_close_matches(row, categories, n=1, cutoff=cutoff)

    if len(result) > 0:
        return result[0]
    return row


def get_enum_from_string(string: str, enum_class, prefix: str | None = None):
    max_similarity = 0.95
    closest_enum = None
    if prefix is None:
        prefix = ""

    # We lower to do a caseinsensitive mapping.
    requested_string = (prefix + string).lower()
    for enum_member in enum_class:
        enum_member_string = enum_member.lower()
        similarity = difflib.SequenceMatcher(None, requested_string, enum_member_string).ratio()
        if similarity > max_similarity:
            max_similarity = similarity
            closest_enum = enum_member
    if closest_enum is None:
        raise KeyError(f"No matching enum found for string '{string}'")
    return closest_enum


def custom_attrgetter(component, category_attribute):
    try:
        category = attrgetter(category_attribute)(component)
    except AttributeError:
        category = category_attribute
    return category


def _unnest_all(schema, separator):
    def _unnest(schema, path=[]):
        for name, dtype in schema.items():
            base_type = dtype.base_type()

            if base_type == pl.Struct:
                yield from _unnest(dtype.to_schema(), [*path, name])
            else:
                yield [*path, name], dtype

    for (col, *fields), dtype in _unnest(schema):
        expr = pl.col(col)

        for field in fields:
            expr = expr.struct[field]

        if col == "":
            name = separator.join(fields)
        else:
            name = separator.join([col, *fields])

        yield expr.alias(name)


def unnest_all(df, separator="."):  # noqa: D103
    return df.select(_unnest_all(df.schema, separator))


def batched(lst, n):
    it = iter(lst)
    return iter(lambda: tuple(islice(it, n)), ())


def get_property_magnitude(property_value, to_unit: str | None = None) -> float:
    """Return magnitude with the given units for a pint Quantity.

    Parameters
    ----------
    property_name

    property_value
        pint.Quantity to extract magnitude from
    to_unit
        String that contains the unit conversion desired. Unit must be compatible.
    """
    if not isinstance(property_value, pint.Quantity | BaseQuantity):
        return property_value
    if to_unit:
        unit = to_unit.replace("$", "usd")  # Dollars are named usd on pint
        property_value = property_value.to(unit)
    return property_value.magnitude


def get_pint_unit(unit: str | None):
    """Parse and convert unit, handling unsupported or empty units."""
    if unit is None:
        return
    unit = unit.replace("$", "usd")
    if unit != "-":
        try:
            return getattr(ureg[unit], "units")
        except UndefinedUnitError:
            return None
    return None


def haskey(base_dict: dict, path: list[str]) -> bool:
    """Return True if the dictionary has the key for the given path."""
    try:
        functools.reduce(lambda x, y: x[y], path, base_dict)
        return True
    except (KeyError, TypeError):
        return False


DEFAULT_COLUMN_MAP = read_json("r2x/defaults/config.json").get("default_column_mapping")
mapping_schema = json.loads(files("r2x.defaults").joinpath("mapping_schema.json").read_text())
