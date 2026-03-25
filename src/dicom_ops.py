"""
DICOM operations for PET DICOM Fixer.

Handles reading, validating, and correcting PET DICOM files.
Supports radionuclide correction (branching ratio + metadata)
and general tag editing with JSON backup.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

import pydicom

from src.radionuclides import RADIONUCLIDES, detect_nuclide_from_halflife

# Tags editable from the GUI
EDITABLE_TAGS = {
    "PatientWeight": ("0010,1030", "Patient Weight (kg)", float),
    "PatientSize": ("0010,1020", "Patient Height (m)", float),
    "PatientSex": ("0010,0040", "Patient Sex (M/F)", str),
    "AcquisitionDate": ("0008,0022", "Acquisition Date (YYYYMMDD)", str),
    "AcquisitionTime": ("0008,0032", "Acquisition Time (HHMMSS)", str),
}

EDITABLE_RADIO_TAGS = {
    "RadionuclideTotalDose": ("0018,1074", "Injected Dose (Bq)", float),
    "RadiopharmaceuticalStartTime": ("0018,1072", "Injection Time (HHMMSS)", str),
    "RadiopharmaceuticalStartDateTime": ("0018,1078", "Injection DateTime", str),
    "RadionuclideHalfLife": ("0018,1075", "Half-Life (s)", float),
}

BQ_PER_MCI = 37_000_000

# User-friendly tag display config
TAG_DISPLAY = {
    "PatientWeight":      {"label": "Weight",              "unit": "kg",  "fmt": "float1"},
    "PatientSize":        {"label": "Height",              "unit": "m",   "fmt": "float2"},
    "PatientSex":         {"label": "Sex",                 "unit": "",    "fmt": "sex"},
    "AcquisitionDate":    {"label": "Scan Date",           "unit": "",    "fmt": "date"},
    "AcquisitionTime":    {"label": "Scan Time",           "unit": "",    "fmt": "time"},
    "RadionuclideTotalDose":            {"label": "Injected Activity",   "unit": "mCi  (1 mCi = 37 MBq)", "fmt": "dose_mci"},
    "RadiopharmaceuticalStartTime":     {"label": "Injection Time",      "unit": "",    "fmt": "time"},
    "RadiopharmaceuticalStartDateTime": {"label": "Injection Date+Time", "unit": "",    "fmt": "datetime"},
    "RadionuclideHalfLife":             {"label": "Half-Life",           "unit": "s",   "fmt": "halflife"},
}


def format_tag_value(tag_name: str, raw_value) -> str:
    """Format a raw DICOM tag value for user-friendly display."""
    if raw_value is None or str(raw_value).strip() == "":
        return ""

    cfg = TAG_DISPLAY.get(tag_name)
    if not cfg:
        return str(raw_value)

    fmt = cfg["fmt"]
    val = str(raw_value).strip()

    if fmt == "float1":
        try:
            return f"{float(val):.1f}"
        except (ValueError, TypeError):
            return val
    elif fmt == "float2":
        try:
            return f"{float(val):.2f}"
        except (ValueError, TypeError):
            return val
    elif fmt == "sex":
        return val.upper()[:1] if val else ""
    elif fmt == "date":
        # YYYYMMDD → DD.MM.YYYY
        if len(val) >= 8 and val[:8].isdigit():
            return f"{val[6:8]}.{val[4:6]}.{val[:4]}"
        return val
    elif fmt == "time":
        # HHMMSS.fff → HH:MM:SS
        clean = val.split(".")[0]
        if len(clean) >= 6 and clean[:6].isdigit():
            return f"{clean[:2]}:{clean[2:4]}:{clean[4:6]}"
        return val
    elif fmt == "datetime":
        # YYYYMMDDHHMMSS → DD.MM.YYYY HH:MM:SS
        if len(val) >= 14:
            return f"{val[6:8]}.{val[4:6]}.{val[:4]} {val[8:10]}:{val[10:12]}:{val[12:14]}"
        return val
    elif fmt == "dose_mci":
        try:
            mci = float(val) / BQ_PER_MCI
            return f"{mci:.4f}" if mci > 0 else ""
        except (ValueError, TypeError):
            return val
    elif fmt == "dose_mbq":
        try:
            mbq = float(val) / 1e6
            return f"{mbq:.2f}" if mbq > 0 else ""
        except (ValueError, TypeError):
            return val
    elif fmt == "halflife":
        try:
            s = float(val)
            if s > 3600:
                return f"{s:.1f}  ({s/3600:.1f} h)"
            elif s > 60:
                return f"{s:.1f}  ({s/60:.1f} min)"
            return f"{s:.1f}"
        except (ValueError, TypeError):
            return val
    return str(raw_value)


def parse_tag_input(tag_name: str, user_input: str) -> str:
    """Convert user-friendly input back to DICOM format."""
    cfg = TAG_DISPLAY.get(tag_name)
    if not cfg:
        return user_input

    fmt = cfg["fmt"]
    val = user_input.strip()

    if fmt == "date":
        # DD.MM.YYYY → YYYYMMDD
        parts = val.replace("/", ".").replace("-", ".").split(".")
        if len(parts) == 3 and len(parts[2]) == 4:
            return f"{parts[2]}{parts[1]}{parts[0]}"
        return val
    elif fmt == "time":
        # HH:MM:SS → HHMMSS
        return val.replace(":", "")
    elif fmt == "datetime":
        # DD.MM.YYYY HH:MM:SS → YYYYMMDDHHMMSS
        parts = val.split()
        if len(parts) == 2:
            date_part = parse_tag_input("_date", parts[0])
            # manual date parse
            dp = parts[0].replace("/", ".").replace("-", ".").split(".")
            time_part = parts[1].replace(":", "")
            if len(dp) == 3 and len(dp[2]) == 4:
                return f"{dp[2]}{dp[1]}{dp[0]}{time_part}"
        return val
    elif fmt == "dose_mci":
        # mCi → Bq
        try:
            return str(float(val) * BQ_PER_MCI)
        except ValueError:
            return val
    elif fmt == "dose_mbq":
        # MBq → Bq
        try:
            return str(float(val) * 1e6)
        except ValueError:
            return val
    elif fmt == "halflife":
        # User might enter "230760" or "230760  (64.1 h)" — take first number
        num_part = val.split()[0] if val else val
        return num_part

    return val


def find_dicom_files(folder: Path) -> list[Path]:
    """Find and validate DICOM image files in a folder.

    Skips non-image DICOM objects (e.g. RWV, Presentation State)
    by checking for the Rows tag which only image objects have.
    """
    files = []
    for f in sorted(folder.iterdir()):
        if f.is_file() and not f.name.startswith("."):
            try:
                ds = pydicom.dcmread(str(f), stop_before_pixels=True)
                if hasattr(ds, "Rows"):
                    files.append(f)
            except Exception:
                continue
    return files


def read_dicom_info(folder: Path) -> dict:
    """
    Read comprehensive DICOM info from the first file in folder.

    Returns dict with patient info, radionuclide info, calibration info,
    and detected issues.
    """
    files = find_dicom_files(folder)
    if not files:
        raise FileNotFoundError(f"No DICOM files found in: {folder}")

    ds = pydicom.dcmread(str(files[0]))
    info = {
        "file_count": len(files),
        "patient_name": str(getattr(ds, "PatientName", "Unknown")),
        "patient_id": str(getattr(ds, "PatientID", "")),
        "patient_weight": float(getattr(ds, "PatientWeight", 0)),
        "patient_size": float(getattr(ds, "PatientSize", 0)),
        "patient_sex": str(getattr(ds, "PatientSex", "")),
        "study_date": str(getattr(ds, "StudyDate", "")),
        "study_description": str(getattr(ds, "StudyDescription", "")),
        "series_description": str(getattr(ds, "SeriesDescription", "")),
        "protocol_name": str(getattr(ds, "ProtocolName", "")),
        "institution": str(getattr(ds, "InstitutionName", "")),
        "modality": str(getattr(ds, "Modality", "")),
        "rescale_slope": float(getattr(ds, "RescaleSlope", 1)),
        "rescale_intercept": float(getattr(ds, "RescaleIntercept", 0)),
        "units": str(getattr(ds, "Units", "")),
        "decay_factor": float(getattr(ds, "DecayFactor", 1)),
        "decay_correction": str(getattr(ds, "DecayCorrection", "")),
        "reconstruction_method": str(getattr(ds, "ReconstructionMethod", "")),
        "frame_duration_ms": int(getattr(ds, "ActualFrameDuration", 0)),
        "dose_calibration_factor": float(getattr(ds, "DoseCalibrationFactor", 0)),
    }

    # Radiopharmaceutical sequence info
    radio_seq = getattr(ds, "RadiopharmaceuticalInformationSequence", None)
    if radio_seq and len(radio_seq) > 0:
        radio = radio_seq[0]
        info["half_life_s"] = float(getattr(radio, "RadionuclideHalfLife", 0))
        info["positron_fraction"] = float(getattr(radio, "RadionuclidePositronFraction", 0))
        info["injected_dose_bq"] = float(getattr(radio, "RadionuclideTotalDose", 0))
        info["pharma_name"] = str(getattr(radio, "Radiopharmaceutical", ""))
        info["injection_time"] = str(getattr(radio, "RadiopharmaceuticalStartTime", "") or "")
        info["injection_datetime"] = str(getattr(radio, "RadiopharmaceuticalStartDateTime", "") or "")

        # Radionuclide code
        code_seq = getattr(radio, "RadionuclideCodeSequence", None)
        if code_seq and len(code_seq) > 0:
            info["nuclide_code_value"] = str(getattr(code_seq[0], "CodeValue", ""))
            info["nuclide_code_meaning"] = str(getattr(code_seq[0], "CodeMeaning", ""))
        else:
            info["nuclide_code_value"] = ""
            info["nuclide_code_meaning"] = ""
    else:
        info["half_life_s"] = 0
        info["positron_fraction"] = 0
        info["injected_dose_bq"] = 0
        info["pharma_name"] = ""
        info["injection_time"] = ""
        info["injection_datetime"] = ""
        info["nuclide_code_value"] = ""
        info["nuclide_code_meaning"] = ""

    # Detect radionuclide from half-life
    info["detected_nuclide"] = detect_nuclide_from_halflife(info["half_life_s"])

    # Calculate decay time (injection → scan)
    info["decay_time_s"] = 0
    try:
        inj_time = info.get("injection_time", "")
        inj_datetime = info.get("injection_datetime", "")
        scan_time = str(getattr(ds, "AcquisitionTime", "") or "")
        scan_date = str(getattr(ds, "AcquisitionDate", getattr(ds, "SeriesDate", "")) or "")

        if inj_datetime and len(inj_datetime) >= 14:
            inj_dt = _parse_dicom_datetime(inj_datetime[:8], inj_datetime[8:])
        elif inj_time:
            series_date = str(getattr(ds, "SeriesDate", getattr(ds, "AcquisitionDate", "")) or "")
            inj_dt = _parse_dicom_datetime(series_date, inj_time)
        else:
            inj_dt = None

        if scan_time and scan_date:
            scan_dt = _parse_dicom_datetime(scan_date, scan_time)
        else:
            scan_dt = None

        if inj_dt and scan_dt:
            dt = (scan_dt - inj_dt).total_seconds()
            if dt < 0:
                dt += 86400
            info["decay_time_s"] = dt
    except Exception:
        pass

    return info


def read_editable_tags(folder: Path) -> dict:
    """Read all editable tag values from the first DICOM file."""
    files = find_dicom_files(folder)
    if not files:
        return {}

    ds = pydicom.dcmread(str(files[0]))
    result = {}

    for tag_name in EDITABLE_TAGS:
        result[tag_name] = getattr(ds, tag_name, None)

    radio_seq = getattr(ds, "RadiopharmaceuticalInformationSequence", None)
    if radio_seq and len(radio_seq) > 0:
        radio = radio_seq[0]
        for tag_name in EDITABLE_RADIO_TAGS:
            result[tag_name] = getattr(radio, tag_name, None)

    return result


def calculate_suvmax(folder: Path) -> dict:
    """
    Calculate SUVmax from PET DICOM series.

    Returns dict with:
        "suvmax": float or None
        "max_bqml": float (max Bq/mL in volume)
        "max_pixel": int (max raw pixel value)
        "suv_factor": float or None
        "error": str or None (reason if SUVmax cannot be calculated)
    """
    import math

    files = find_dicom_files(folder)
    if not files:
        return {"suvmax": None, "error": "No DICOM files found"}

    # Read first file for parameters
    ds0 = pydicom.dcmread(str(files[0]))
    slope = float(getattr(ds0, "RescaleSlope", 1))
    intercept = float(getattr(ds0, "RescaleIntercept", 0))
    weight_kg = float(getattr(ds0, "PatientWeight", 0))

    radio_seq = getattr(ds0, "RadiopharmaceuticalInformationSequence", None)
    dose_bq = 0.0
    half_life_s = 0.0
    decay_time_s = 0.0

    if radio_seq and len(radio_seq) > 0:
        radio = radio_seq[0]
        dose_bq = float(getattr(radio, "RadionuclideTotalDose", 0))
        half_life_s = float(getattr(radio, "RadionuclideHalfLife", 0))

        # Calculate decay time
        inj_time = getattr(radio, "RadiopharmaceuticalStartTime", None)
        inj_datetime = getattr(radio, "RadiopharmaceuticalStartDateTime", None)
        scan_time = getattr(ds0, "AcquisitionTime", None)
        scan_date = getattr(ds0, "AcquisitionDate", getattr(ds0, "SeriesDate", None))

        try:
            if inj_datetime and len(str(inj_datetime)) >= 14:
                dt_str = str(inj_datetime)
                inj_dt = _parse_dicom_datetime(dt_str[:8], dt_str[8:])
            elif inj_time:
                series_date = str(getattr(ds0, "SeriesDate", getattr(ds0, "AcquisitionDate", "")))
                inj_dt = _parse_dicom_datetime(series_date, str(inj_time))
            else:
                inj_dt = None

            if scan_time and scan_date:
                scan_dt = _parse_dicom_datetime(str(scan_date), str(scan_time))
            else:
                scan_dt = None

            if inj_dt and scan_dt:
                decay_time_s = (scan_dt - inj_dt).total_seconds()
                if decay_time_s < 0:
                    decay_time_s += 86400
        except Exception:
            pass

    # Find max Bq/mL across all slices (each slice may have its own RescaleSlope)
    max_bqml = 0.0
    max_pixel = 0
    for f in files:
        ds = pydicom.dcmread(str(f))
        px = ds.pixel_array
        file_max = int(px.max())
        slice_slope = float(getattr(ds, "RescaleSlope", slope))
        slice_intercept = float(getattr(ds, "RescaleIntercept", intercept))
        file_bqml = file_max * slice_slope + slice_intercept
        if file_bqml > max_bqml:
            max_bqml = file_bqml
            max_pixel = file_max

    # Check if SUV can be calculated
    result = {"max_bqml": max_bqml, "max_pixel": max_pixel, "suvmax": None, "suv_factor": None, "error": None}

    if weight_kg <= 0:
        result["error"] = "Missing patient weight"
        return result
    if dose_bq <= 0:
        result["error"] = "Missing injected dose"
        return result
    if half_life_s <= 0:
        result["error"] = "Missing half-life"
        return result
    if decay_time_s <= 0:
        result["error"] = "Missing injection/scan time"
        return result

    # Decay-corrected dose
    decay_factor = 2 ** (-decay_time_s / half_life_s)
    corrected_dose_bq = dose_bq * decay_factor

    # SUV factor: weight_g / corrected_dose_bq
    suv_factor = (weight_kg * 1000) / corrected_dose_bq
    suvmax = max_bqml * suv_factor

    result["suv_factor"] = suv_factor
    result["suvmax"] = suvmax
    return result


def _parse_dicom_datetime(date_str: str, time_str: str) -> datetime:
    """Parse DICOM date + time strings into datetime."""
    date_str = str(date_str).strip()
    time_str = str(time_str).strip()
    if "." in time_str:
        main_part, frac = time_str.split(".")
        frac = frac[:6].ljust(6, "0")
        time_str = f"{main_part}.{frac}"
        return datetime.strptime(f"{date_str}{time_str}", "%Y%m%d%H%M%S.%f")
    return datetime.strptime(f"{date_str}{time_str}", "%Y%m%d%H%M%S")


def apply_corrections(
    input_folder: Path,
    output_folder: Path,
    tag_changes: dict | None = None,
    nuclide_correction: dict | None = None,
) -> int:
    """
    Apply corrections to DICOM files and save to a new folder.

    Args:
        input_folder: Source DICOM folder
        output_folder: Destination folder (will be created)
        tag_changes: Dict of {tag_name: new_value} for general tag edits
        nuclide_correction: Dict with:
            - "source": current nuclide name (e.g. "F-18")
            - "target": actual nuclide name (e.g. "Y-90")
            - "source": source radionuclide name
            - "target": target radionuclide name

    Returns:
        Number of files corrected
    """
    files = find_dicom_files(input_folder)
    if not files:
        raise FileNotFoundError(f"No DICOM files found in: {input_folder}")

    output_folder.mkdir(parents=True, exist_ok=True)
    tag_changes = tag_changes or {}

    # Prepare nuclide correction parameters
    br_factor = 1.0
    target_data = None

    if nuclide_correction:
        source = nuclide_correction["source"]
        target = nuclide_correction["target"]
        target_data = RADIONUCLIDES[target]
        source_data = RADIONUCLIDES[source]
        br_factor = source_data["positron_br"] / target_data["positron_br"]

    # Save backup of original values
    _save_backup(input_folder, files[0], tag_changes, nuclide_correction, output_folder)

    # Process each file
    count = 0
    for f in files:
        ds = pydicom.dcmread(str(f))

        # Apply general tag changes
        _apply_tag_changes(ds, tag_changes)

        # Apply nuclide correction
        if nuclide_correction and target_data:
            _apply_nuclide_correction(ds, target_data, br_factor)

        # Save to output folder
        out_path = output_folder / f.name
        ds.save_as(str(out_path))
        count += 1

    return count


def _apply_tag_changes(ds: pydicom.Dataset, changes: dict):
    """Apply general tag edits to a DICOM dataset."""
    main_changes = {}
    radio_changes = {}

    for tag_name, value in changes.items():
        if tag_name in EDITABLE_RADIO_TAGS:
            tag_type = EDITABLE_RADIO_TAGS[tag_name][2]
            radio_changes[tag_name] = tag_type(value)
        elif tag_name in EDITABLE_TAGS:
            tag_type = EDITABLE_TAGS[tag_name][2]
            main_changes[tag_name] = tag_type(value)

    for tag_name, value in main_changes.items():
        setattr(ds, tag_name, value)

    if radio_changes:
        radio_seq = getattr(ds, "RadiopharmaceuticalInformationSequence", None)
        if radio_seq and len(radio_seq) > 0:
            for tag_name, value in radio_changes.items():
                setattr(radio_seq[0], tag_name, value)


def _apply_nuclide_correction(
    ds: pydicom.Dataset,
    target_data: dict,
    br_factor: float,
):
    """Apply radionuclide correction to a DICOM dataset."""
    # Fix RescaleSlope with branching ratio (abundance) correction
    old_slope = float(getattr(ds, "RescaleSlope", 1))
    new_slope = old_slope * br_factor
    ds.RescaleSlope = f"{new_slope:.10g}"

    # 3. Update RadiopharmaceuticalInformationSequence
    radio_seq = getattr(ds, "RadiopharmaceuticalInformationSequence", None)
    if radio_seq and len(radio_seq) > 0:
        radio = radio_seq[0]
        radio.RadionuclideHalfLife = target_data["half_life_s"]
        radio.RadionuclidePositronFraction = target_data["positron_br"]
        radio.Radiopharmaceutical = target_data["pharma_name"]

        # Update RadionuclideCodeSequence
        code_seq = getattr(radio, "RadionuclideCodeSequence", None)
        if code_seq and len(code_seq) > 0:
            code_seq[0].CodeValue = target_data["dicom_code_value"]
            code_seq[0].CodeMeaning = target_data["dicom_code_meaning"]
            code_seq[0].CodingSchemeDesignator = target_data["coding_scheme"]

        # Update RadiopharmaceuticalCodeSequence
        pharma_code_seq = getattr(radio, "RadiopharmaceuticalCodeSequence", None)
        if pharma_code_seq and len(pharma_code_seq) > 0:
            pharma_code_seq[0].CodeMeaning = target_data["pharma_name"]


def _save_backup(
    input_folder: Path,
    first_file: Path,
    tag_changes: dict,
    nuclide_correction: dict | None,
    output_folder: Path,
):
    """Save a JSON backup of original values and applied changes."""
    ds = pydicom.dcmread(str(first_file), stop_before_pixels=True)

    original_values = {}
    original_values["RescaleSlope"] = str(getattr(ds, "RescaleSlope", ""))
    original_values["DecayFactor"] = str(getattr(ds, "DecayFactor", ""))

    radio_seq = getattr(ds, "RadiopharmaceuticalInformationSequence", None)
    if radio_seq and len(radio_seq) > 0:
        radio = radio_seq[0]
        original_values["RadionuclideHalfLife"] = str(getattr(radio, "RadionuclideHalfLife", ""))
        original_values["RadionuclidePositronFraction"] = str(
            getattr(radio, "RadionuclidePositronFraction", "")
        )
        original_values["Radiopharmaceutical"] = str(getattr(radio, "Radiopharmaceutical", ""))

    for tag_name in tag_changes:
        if tag_name in EDITABLE_TAGS:
            original_values[tag_name] = str(getattr(ds, tag_name, ""))
        elif tag_name in EDITABLE_RADIO_TAGS and radio_seq and len(radio_seq) > 0:
            original_values[tag_name] = str(getattr(radio_seq[0], tag_name, ""))

    backup = {
        "correction_time": datetime.now().isoformat(),
        "source_folder": str(input_folder),
        "original_values": original_values,
        "tag_changes": {k: str(v) for k, v in tag_changes.items()} if tag_changes else {},
        "nuclide_correction": nuclide_correction,
    }

    backup_path = output_folder / "_correction_backup.json"
    if backup_path.exists():
        existing = json.loads(backup_path.read_text(encoding="utf-8"))
        if isinstance(existing, list):
            existing.append(backup)
        else:
            existing = [existing, backup]
        backup_path.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
    else:
        backup_path.write_text(
            json.dumps([backup], indent=2, ensure_ascii=False), encoding="utf-8"
        )
