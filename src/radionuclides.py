"""
Radionuclide database for PET DICOM correction.

Contains half-life, positron branching ratio, and DICOM coding
for common PET radionuclides.
"""

from __future__ import annotations

RADIONUCLIDES = {
    "F-18": {
        "half_life_s": 6586.2,
        "positron_br": 0.9686,
        "dicom_code_value": "C-111A1",
        "dicom_code_meaning": "^18^Fluorine",
        "coding_scheme": "SRT",
        "pharma_name": "Fluorodeoxyglucose F^18^",
    },
    "Ga-68": {
        "half_life_s": 4070.4,
        "positron_br": 0.8883,
        "dicom_code_value": "C-131A3",
        "dicom_code_meaning": "^68^Gallium",
        "coding_scheme": "SRT",
        "pharma_name": "Gallium-68",
    },
    "Y-90": {
        "half_life_s": 230760.0,
        "positron_br": 3.186e-5,
        "dicom_code_meaning": "^90^Yttrium",
        "dicom_code_value": "C-163A4",
        "coding_scheme": "SRT",
        "pharma_name": "Yttrium-90 microspheres",
    },
    "Cu-64": {
        "half_life_s": 45720.0,
        "positron_br": 0.1752,
        "dicom_code_value": "C-130A3",
        "dicom_code_meaning": "^64^Copper",
        "coding_scheme": "SRT",
        "pharma_name": "Copper-64",
    },
}


def get_branching_ratio_correction(source_nuclide: str, target_nuclide: str) -> float:
    """
    Calculate the branching ratio correction factor.

    When a scanner is calibrated with source_nuclide but actually imaged
    target_nuclide, multiply RescaleSlope by this factor.

    Args:
        source_nuclide: Radionuclide in DICOM header (e.g. "F-18")
        target_nuclide: Actual radionuclide (e.g. "Y-90")

    Returns:
        Correction factor (BR_source / BR_target)
    """
    br_source = RADIONUCLIDES[source_nuclide]["positron_br"]
    br_target = RADIONUCLIDES[target_nuclide]["positron_br"]
    return br_source / br_target


def detect_nuclide_from_halflife(half_life_s: float, tolerance: float = 0.05) -> str | None:
    """
    Identify radionuclide from half-life value.

    Args:
        half_life_s: Half-life in seconds from DICOM
        tolerance: Relative tolerance for matching (default 5%)

    Returns:
        Nuclide name or None if no match
    """
    for name, data in RADIONUCLIDES.items():
        expected = data["half_life_s"]
        if abs(half_life_s - expected) / expected < tolerance:
            return name
    return None
