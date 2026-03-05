"""
PET DICOM Fixer — Main GUI Application.

Corrects technician errors in PET DICOM headers, including
radionuclide mislabeling (e.g. F-18 label on Y-90 PET scans)
with automatic branching ratio correction.
"""

from __future__ import annotations

import math
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from src.dicom_ops import (
    BQ_PER_MCI,
    EDITABLE_RADIO_TAGS,
    EDITABLE_TAGS,
    TAG_DISPLAY,
    apply_corrections,
    calculate_suvmax,
    find_dicom_files,
    format_tag_value,
    parse_tag_input,
    read_dicom_info,
    read_editable_tags,
)
from src.radionuclides import RADIONUCLIDES, get_branching_ratio_correction

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# Colors
GREEN = "#2ecc71"
RED = "#e74c3c"
YELLOW = "#f1c40f"
CYAN = "#00bcd4"
GRAY = "#7f8c8d"
MUTED = "#95a5a6"
DARK_BG = "#16213e"
ENTRY_BORDER = "#565B5E"
PANEL_BG = "#1e1e2e"


class PETDicomFixerApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("PET DICOM Fixer")
        self.geometry("900x720")
        self.minsize(820, 620)

        self.dicom_folder = None
        self.dicom_info = None
        self.suv_result = None
        self.tag_entries = {}
        self.original_formatted = {}

        self._build_ui()

    def _build_ui(self):
        # ── Title bar ──
        title_bar = ctk.CTkFrame(self, fg_color="transparent", height=44)
        title_bar.pack(fill="x", padx=20, pady=(12, 0))
        title_bar.pack_propagate(False)

        ctk.CTkLabel(
            title_bar, text="PET DICOM Fixer",
            font=ctk.CTkFont(size=22, weight="bold"),
        ).pack(side="left")
        ctk.CTkLabel(
            title_bar, text="v1.0",
            font=ctk.CTkFont(size=11), text_color=GRAY,
        ).pack(side="left", padx=(8, 0), pady=(6, 0))

        # ── Loader bar ──
        loader = ctk.CTkFrame(self)
        loader.pack(fill="x", padx=20, pady=(8, 0))

        ctk.CTkButton(
            loader, text="Browse...", command=self._browse_folder,
            width=100, height=32, fg_color=CYAN, hover_color="#0097a7",
            font=ctk.CTkFont(size=13),
        ).pack(side="left", padx=(10, 8), pady=8)

        self.folder_label = ctk.CTkLabel(
            loader, text="No folder selected",
            text_color=GRAY, font=ctk.CTkFont(size=12),
        )
        self.folder_label.pack(side="left", fill="x", expand=True)

        self.status_label = ctk.CTkLabel(
            loader, text="", font=ctk.CTkFont(size=12, weight="bold"),
        )
        self.status_label.pack(side="right", padx=10)

        # ── Tab view ──
        self.tabs = ctk.CTkTabview(self, anchor="nw")
        self.tabs.pack(fill="both", expand=True, padx=20, pady=(8, 0))
        self.tabs.add("Correction")
        self.tabs.add("Tag Editor")
        self.tabs.add("Calculator")

        self._build_correction_tab(self.tabs.tab("Correction"))
        self._build_tag_editor_tab(self.tabs.tab("Tag Editor"))
        self._build_calculator_tab(self.tabs.tab("Calculator"))

        # ── Action buttons (always visible) ──
        action_bar = ctk.CTkFrame(self, fg_color="transparent")
        action_bar.pack(fill="x", padx=20, pady=(8, 14))

        ctk.CTkButton(
            action_bar, text="Preview Changes", command=self._preview_changes,
            width=170, height=38, fg_color="#2c3e50", hover_color="#34495e",
            font=ctk.CTkFont(size=13),
        ).pack(side="left", padx=5)

        ctk.CTkButton(
            action_bar, text="Apply && Save", command=self._apply_and_save,
            width=170, height=38, fg_color="#27ae60", hover_color="#219a52",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(side="right", padx=5)

        # ── Footer ──
        ctk.CTkLabel(
            self, text="\u00a9 2026 cemturkan97",
            font=ctk.CTkFont(size=11), text_color=GRAY,
        ).pack(pady=(0, 8))

    # ── Correction Tab ─────────────────────────────
    def _build_correction_tab(self, parent):
        # Use scrollable frame so everything fits even on smaller screens
        scroll = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        scroll.pack(fill="both", expand=True)

        # ── Info section ──
        ctk.CTkLabel(
            scroll, text="Current DICOM Info",
            font=ctk.CTkFont(size=15, weight="bold"),
        ).pack(anchor="w", pady=(2, 4))

        self.info_frame = ctk.CTkFrame(scroll, corner_radius=8)
        self.info_frame.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(
            self.info_frame, text="Load a DICOM folder to see info",
            text_color=GRAY, font=ctk.CTkFont(size=12),
        ).pack(pady=15)

        # ── Separator ──
        ctk.CTkFrame(scroll, height=2, fg_color="#3d3d5c").pack(fill="x", pady=6)

        # ── Radionuclide correction section ──
        ctk.CTkLabel(
            scroll, text="Radionuclide Correction",
            font=ctk.CTkFont(size=15, weight="bold"),
        ).pack(anchor="w", pady=(6, 4))

        self.current_nuclide_label = ctk.CTkLabel(
            scroll, text="DICOM says: — (load DICOM first)",
            font=ctk.CTkFont(size=13), text_color=GRAY,
        )
        self.current_nuclide_label.pack(anchor="w", pady=2)

        # Target selector row
        sel_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        sel_frame.pack(fill="x", pady=5)

        ctk.CTkLabel(
            sel_frame, text="Actual radionuclide:",
            font=ctk.CTkFont(size=13),
        ).pack(side="left")

        self.target_nuclide_var = ctk.StringVar(value="Y-90")
        self.target_combo = ctk.CTkComboBox(
            sel_frame, values=list(RADIONUCLIDES.keys()),
            variable=self.target_nuclide_var,
            command=self._update_correction_preview, width=140,
        )
        self.target_combo.pack(side="left", padx=(10, 8))

        self.target_info_label = ctk.CTkLabel(
            sel_frame, text="", font=ctk.CTkFont(size=11), text_color=GRAY,
        )
        self.target_info_label.pack(side="left")

        # Correction preview box
        self.correction_frame = ctk.CTkFrame(scroll, fg_color=DARK_BG, corner_radius=8)
        self.correction_frame.pack(fill="x", pady=8)
        self.correction_label = ctk.CTkLabel(
            self.correction_frame, text="Select target to see correction preview",
            text_color=GRAY, font=ctk.CTkFont(size=12, family="Courier"),
            justify="left",
        )
        self.correction_label.pack(anchor="w", padx=12, pady=10)

        # Checkboxes row
        chk_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        chk_frame.pack(fill="x", pady=(0, 5))

        self.apply_nuclide_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            chk_frame, text="Apply correction",
            variable=self.apply_nuclide_var,
            font=ctk.CTkFont(weight="bold"),
        ).pack(side="left")

        # Show target info on startup
        self._update_correction_preview()

    # ── Calculator Tab ────────────────────────────
    def _build_calculator_tab(self, parent):
        scroll = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        scroll.pack(fill="both", expand=True)

        # ── Header with Reset button ──
        calc_header = ctk.CTkFrame(scroll, fg_color="transparent")
        calc_header.pack(fill="x", pady=(2, 4))
        ctk.CTkLabel(
            calc_header, text="Radionuclide Correction",
            font=ctk.CTkFont(size=15, weight="bold"),
        ).pack(side="left")
        ctk.CTkButton(
            calc_header, text="Reset", width=70,
            fg_color=BTN_FG, hover_color=BTN_HOVER,
            command=self._reset_calculator,
        ).pack(side="right")

        nuc_frame = ctk.CTkFrame(scroll, corner_radius=8)
        nuc_frame.pack(fill="x", pady=(0, 10))
        nuc_inner = ctk.CTkFrame(nuc_frame, fg_color="transparent")
        nuc_inner.pack(fill="x", padx=12, pady=10)

        nuclide_keys = list(RADIONUCLIDES.keys())

        ctk.CTkLabel(nuc_inner, text="Scanner protocol:", font=ctk.CTkFont(size=13)).grid(
            row=0, column=0, sticky="w", pady=3)
        self.calc_source_var = ctk.StringVar(value="F-18")
        ctk.CTkComboBox(
            nuc_inner, values=nuclide_keys, variable=self.calc_source_var,
            command=lambda _: self._update_calculator(), width=130,
        ).grid(row=0, column=1, padx=(10, 30), pady=3)

        ctk.CTkLabel(nuc_inner, text="Actual nuclide:", font=ctk.CTkFont(size=13)).grid(
            row=0, column=2, sticky="w", pady=3)
        self.calc_target_var = ctk.StringVar(value="Y-90")
        ctk.CTkComboBox(
            nuc_inner, values=nuclide_keys, variable=self.calc_target_var,
            command=lambda _: self._update_calculator(), width=130,
        ).grid(row=0, column=3, padx=(10, 0), pady=3)

        self.calc_br_label = ctk.CTkLabel(
            nuc_frame, text="",
            font=ctk.CTkFont(size=16, weight="bold"), text_color=YELLOW,
        )
        self.calc_br_label.pack(anchor="w", padx=12, pady=(2, 2))

        self.calc_br_detail = ctk.CTkLabel(
            nuc_frame, text="", font=ctk.CTkFont(size=11), text_color=GRAY,
        )
        self.calc_br_detail.pack(anchor="w", padx=12, pady=(0, 6))

        slope_frame = ctk.CTkFrame(nuc_frame, fg_color="transparent")
        slope_frame.pack(fill="x", padx=12, pady=(0, 10))
        ctk.CTkLabel(slope_frame, text="RescaleSlope:", font=ctk.CTkFont(size=12)).pack(
            side="left")
        self.calc_slope_entry = ctk.CTkEntry(slope_frame, width=130)
        self.calc_slope_entry.insert(0, "0.000941")
        self.calc_slope_entry.pack(side="left", padx=(8, 6))
        self.calc_slope_result = ctk.CTkLabel(
            slope_frame, text="", font=ctk.CTkFont(size=12, weight="bold"), text_color=GREEN)
        self.calc_slope_result.pack(side="left")
        self.calc_slope_entry.bind("<KeyRelease>", lambda e: self._update_calculator())

        # ── Separator ──
        ctk.CTkFrame(scroll, height=2, fg_color="#3d3d5c").pack(fill="x", pady=8)

        # ── Section 2: SUV Impact ──
        ctk.CTkLabel(
            scroll, text="What happens to SUV if parameters change?",
            font=ctk.CTkFont(size=15, weight="bold"),
        ).pack(anchor="w", pady=(4, 2))
        ctk.CTkLabel(
            scroll,
            text="Change 'After' values to see the effect on SUV.",
            font=ctk.CTkFont(size=11), text_color=GRAY,
        ).pack(anchor="w", pady=(0, 6))

        # Parameter table
        param_frame = ctk.CTkFrame(scroll, corner_radius=8)
        param_frame.pack(fill="x", pady=(0, 8))

        self.calc_entries = {}
        self._calc_dose_label = None

        # Dose unit selector row
        unit_row = ctk.CTkFrame(param_frame, fg_color="transparent")
        unit_row.pack(fill="x", padx=12, pady=(8, 0))
        ctk.CTkLabel(unit_row, text="Dose unit:", font=ctk.CTkFont(size=12)).pack(side="left")
        self.calc_dose_unit_var = ctk.StringVar(value="mCi")
        ctk.CTkSegmentedButton(
            unit_row, values=["mCi", "MBq"], variable=self.calc_dose_unit_var,
            command=self._on_calc_dose_unit_change, width=120,
        ).pack(side="left", padx=(8, 0))

        # Column headers
        hdr_frame = ctk.CTkFrame(param_frame, fg_color="transparent")
        hdr_frame.pack(fill="x", padx=12, pady=(8, 0))
        ctk.CTkLabel(hdr_frame, text="", width=190).pack(side="left")
        ctk.CTkLabel(hdr_frame, text="Before", font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=MUTED, width=100).pack(side="left")
        ctk.CTkLabel(hdr_frame, text="", width=238).pack(side="left")  # stepper + arrow space
        ctk.CTkLabel(hdr_frame, text="After", font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=MUTED, width=100).pack(side="left")

        fields = [
            ("weight", "Weight (kg)", "100", "numeric"),
            ("dose", "Dose (mCi)", "100", "numeric"),
            ("inj_time", "Injection Time (DD:HH:MM)", "00:00:00", "time"),
            ("scan_time", "Scan Time (DD:HH:MM)", "00:01:00", "time"),
        ]

        for key, label, default, ftype in fields:
            row_frame = ctk.CTkFrame(param_frame, fg_color="transparent")
            row_frame.pack(fill="x", padx=12, pady=3)

            # Label
            lbl = ctk.CTkLabel(row_frame, text=f"{label}:", font=ctk.CTkFont(size=12),
                               anchor="w", width=190)
            lbl.pack(side="left")
            if key == "dose":
                self._calc_dose_label = lbl

            # Before entry
            before = ctk.CTkEntry(row_frame, width=100)
            before.insert(0, default)
            before.pack(side="left", padx=(0, 2))
            before.bind("<KeyRelease>", lambda e: self._update_calculator())
            self.calc_entries[f"orig_{key}"] = before

            # Middle: time steppers or spacer (to keep After column aligned)
            if ftype == "time":
                _create_time_stepper(row_frame, before, self._update_calculator,
                                     include_day=True).pack(side="left", padx=4)
            else:
                # Spacer matching time stepper width: 6 buttons × (28+2px) + padx
                ctk.CTkLabel(row_frame, text="", width=192).pack(side="left", padx=4)

            # Arrow
            ctk.CTkLabel(row_frame, text="→", text_color=GRAY,
                         font=ctk.CTkFont(size=14)).pack(side="left", padx=4)

            # After entry
            after = ctk.CTkEntry(row_frame, width=100)
            after.insert(0, default)
            after.pack(side="left", padx=(0, 2))
            after.bind("<KeyRelease>", lambda e: self._update_calculator())
            self.calc_entries[f"new_{key}"] = after

            # After time steppers
            if ftype == "time":
                _create_time_stepper(row_frame, after, self._update_calculator,
                                     include_day=True).pack(side="left", padx=(2, 0))

        # Half-life info (aligned with Before/After columns)
        hl_row = ctk.CTkFrame(param_frame, fg_color="transparent")
        hl_row.pack(fill="x", padx=12, pady=(4, 8))
        ctk.CTkLabel(hl_row, text="Half-Life:", font=ctk.CTkFont(size=12),
                     anchor="w", width=190).pack(side="left")
        self.calc_hl_before = ctk.CTkLabel(
            hl_row, text="", font=ctk.CTkFont(size=12), text_color=CYAN, width=100)
        self.calc_hl_before.pack(side="left", padx=(0, 2))
        ctk.CTkLabel(hl_row, text="", width=192).pack(side="left", padx=4)  # spacer
        ctk.CTkLabel(hl_row, text="→", text_color=GRAY,
                     font=ctk.CTkFont(size=14)).pack(side="left", padx=4)
        self.calc_hl_after = ctk.CTkLabel(
            hl_row, text="", font=ctk.CTkFont(size=12), text_color=CYAN, width=100)
        self.calc_hl_after.pack(side="left", padx=(0, 2))

        # ── Result box ──
        self.calc_result_frame = ctk.CTkFrame(scroll, fg_color=DARK_BG, corner_radius=8)
        self.calc_result_frame.pack(fill="x", pady=(0, 8))
        self.calc_result_label = ctk.CTkLabel(
            self.calc_result_frame, text="",
            text_color=GRAY, font=ctk.CTkFont(size=13, family="Courier"),
            justify="left",
        )
        self.calc_result_label.pack(anchor="w", padx=14, pady=12)

        # Initial update
        self._update_calculator()

    def _reset_calculator(self):
        """Reset all calculator fields to defaults."""
        self.calc_source_var.set("F-18")
        self.calc_target_var.set("Y-90")
        self.calc_dose_unit_var.set("mCi")
        if self._calc_dose_label:
            self._calc_dose_label.configure(text="Dose (mCi):")
        defaults = {
            "weight": "100", "dose": "100",
            "inj_time": "00:00:00", "scan_time": "00:01:00",
        }
        for prefix in ("orig_", "new_"):
            for key, val in defaults.items():
                entry = self.calc_entries.get(f"{prefix}{key}")
                if entry:
                    entry.delete(0, "end")
                    entry.insert(0, val)
        slope_entry = self.calc_slope_entry
        slope_entry.delete(0, "end")
        slope_entry.insert(0, "0.000941")
        self._update_calculator()

    def _on_calc_dose_unit_change(self, new_unit):
        """Update dose label when unit changes. Values stay the same."""
        if self._calc_dose_label:
            self._calc_dose_label.configure(text=f"Dose ({new_unit}):")
        self._update_calculator()

    def _update_calculator(self, _=None):
        """Recalculate all calculator results."""
        source = self.calc_source_var.get()
        target = self.calc_target_var.get()
        source_data = RADIONUCLIDES[source]
        target_data = RADIONUCLIDES[target]

        # ── BR Factor ──
        if source == target:
            self.calc_br_label.configure(
                text="No correction needed (same nuclide)", text_color=GREEN)
            self.calc_br_detail.configure(text="")
            br_factor = 1.0
        else:
            br_factor = get_branching_ratio_correction(source, target)
            self.calc_br_label.configure(
                text=f"Correction factor: {br_factor:,.2f}x",
                text_color=RED if br_factor > 100 else YELLOW)
            self.calc_br_detail.configure(
                text=f"{source} abundance: {source_data['positron_br']:.4%}   |   "
                     f"{target} abundance: {target_data['positron_br']:.6%}")

        # ── RescaleSlope ──
        slope_text = self.calc_slope_entry.get().strip()
        if slope_text:
            try:
                old_slope = float(slope_text)
                new_slope = old_slope * br_factor
                self.calc_slope_result.configure(text=f"→  {new_slope:.6g}")
            except ValueError:
                self.calc_slope_result.configure(text="")
        else:
            self.calc_slope_result.configure(text="")

        # ── Half-life ──
        source_hl = source_data["half_life_s"]
        target_hl = target_data["half_life_s"]
        def _hl_short(hl):
            return f"{hl / 3600:.1f} h" if hl > 3600 else f"{hl / 60:.1f} min"
        self.calc_hl_before.configure(text=_hl_short(source_hl))
        self.calc_hl_after.configure(text=_hl_short(target_hl))

        # ── SUV Impact ──
        try:
            orig_w = float(self.calc_entries["orig_weight"].get())
            new_w = float(self.calc_entries["new_weight"].get())
            orig_d = float(self.calc_entries["orig_dose"].get())
            new_d = float(self.calc_entries["new_dose"].get())
            orig_dt = self._parse_time_diff("orig_")
            new_dt = self._parse_time_diff("new_")

            if orig_w <= 0 or new_w <= 0 or orig_d <= 0 or new_d <= 0:
                self.calc_result_label.configure(
                    text="Enter valid values to see results", text_color=GRAY)
                return

            # Individual effects
            pct_weight = (new_w / orig_w - 1) * 100
            pct_dose = (orig_d / new_d - 1) * 100  # inverse: more dose → lower SUV
            decay_orig = 2 ** (-orig_dt / source_hl)
            decay_new = 2 ** (-new_dt / target_hl)
            pct_decay = (decay_orig / decay_new - 1) * 100

            # Combined SUV change (from parameters only)
            orig_suv = self._calc_suv_factor("orig_", source_hl)
            new_suv = self._calc_suv_factor("new_", target_hl)
            if orig_suv is None or new_suv is None:
                self.calc_result_label.configure(
                    text="Enter valid values to see results", text_color=GRAY)
                return

            suv_ratio = new_suv / orig_suv
            pct_total = (suv_ratio - 1) * 100
            fmt_pct = lambda v: f"+{v:.2f}%" if v >= 0 else f"{v:.2f}%"

            lines = [
                "Parameter effects on SUV:",
                f"  Weight:      {orig_w:.2f} → {new_w:.2f} kg     {fmt_pct(pct_weight)}",
                f"  Dose:        {orig_d:.2f} → {new_d:.2f} {self.calc_dose_unit_var.get()}    {fmt_pct(pct_dose)}",
                f"  Decay time:  {self._fmt_ddhhmm('orig_')} (HL {self._fmt_hl(source_hl)}) → {self._fmt_ddhhmm('new_')} (HL {self._fmt_hl(target_hl)})  {fmt_pct(pct_decay)}",
                "",
                f"SUV change (parameters): {suv_ratio:.4f}x  ({fmt_pct(pct_total)})",
            ]

            if br_factor != 1.0:
                total_mult = br_factor * suv_ratio
                pct_br = (br_factor - 1) * 100
                pct_combined = (total_mult - 1) * 100
                lines.append(f"Radionuclide correction multiplier ({source}→{target}): {br_factor:.4f}x  ({fmt_pct(pct_br)})")
                lines.append("")
                lines.append(f"{suv_ratio:.4f} × {br_factor:.4f} = {total_mult:.4f}x  ({fmt_pct(pct_combined)})")
                lines.append("")
                lines.append(f"  e.g.  SUVmax 5.0  →  {5.0 * total_mult:,.2f}")
                lines.append(f"  e.g.  SUVmax 10.0 →  {10.0 * total_mult:,.2f}")
                lines.append("")
                lines.append("⚠ Note: Changing radionuclide corrects abundance")
                lines.append("and metadata only. The following remain uncorrected")
                lines.append("(baked into reconstruction, require raw sinogram):")
                lines.append("  • Per-frame decay (wrong half-life per bed position)")
                lines.append("  • Positron range (spatial blur differs by nuclide)")
                lines.append("  • Scatter/bremsstrahlung contamination")
                lines.append("These effects are small and systematic. The corrected")
                lines.append("values are significantly closer to reality.")
            else:
                lines.append("")
                lines.append(f"  e.g.  SUVmax 5.0  →  {5.0 * suv_ratio:.2f}")
                lines.append(f"  e.g.  SUVmax 10.0 →  {10.0 * suv_ratio:.2f}")

            self.calc_result_label.configure(text="\n".join(lines), text_color=YELLOW)

        except (ValueError, ZeroDivisionError):
            self.calc_result_label.configure(
                text="Enter valid values to see results", text_color=GRAY)

    def _fmt_ddhhmm(self, prefix):
        """Format time entry as readable string for results."""
        text = self.calc_entries[f"{prefix}scan_time"].get().strip()
        parts = text.replace(".", ":").split(":")
        d = int(parts[0]) if len(parts) > 0 and parts[0] else 0
        h = int(parts[1]) if len(parts) > 1 and parts[1] else 0
        m = int(parts[2]) if len(parts) > 2 and parts[2] else 0
        dt = self._parse_time_diff(prefix)
        hours = dt / 3600
        if d > 0:
            return f"{d}d {h}h {m}m"
        return f"{h}h {m}m"

    @staticmethod
    def _fmt_hl(hl_s):
        """Format half-life as compact string (e.g. '109.8 min' or '64.1 h')."""
        if hl_s > 3600:
            return f"{hl_s / 3600:.1f} h"
        return f"{hl_s / 60:.1f} min"

    def _calc_suv_factor(self, prefix, half_life_s):
        """Calculate SUV factor from entries with given prefix."""
        try:
            weight_kg = float(self.calc_entries[f"{prefix}weight"].get())
            dose_val = float(self.calc_entries[f"{prefix}dose"].get())
            dt_s = self._parse_time_diff(prefix)
            if weight_kg <= 0 or dose_val <= 0:
                return None
            # Convert to Bq based on selected unit
            unit = self.calc_dose_unit_var.get()
            if unit == "MBq":
                dose_bq = dose_val * 1_000_000
            else:
                dose_bq = dose_val * BQ_PER_MCI
            corrected_dose = dose_bq * (2 ** (-dt_s / half_life_s))
            return (weight_kg * 1000) / corrected_dose
        except (ValueError, KeyError):
            return None

    def _parse_time_diff(self, prefix):
        """Parse DD:HH:MM time entries and return scan - injection in seconds."""
        inj_str = self.calc_entries[f"{prefix}inj_time"].get().strip()
        scan_str = self.calc_entries[f"{prefix}scan_time"].get().strip()
        inj_s = self._ddhhmm_to_seconds(inj_str)
        scan_s = self._ddhhmm_to_seconds(scan_str)
        dt = scan_s - inj_s
        if dt < 0:
            dt += 86400
        return dt

    @staticmethod
    def _ddhhmm_to_seconds(text):
        """Convert DD:HH:MM string to total seconds."""
        parts = text.replace(".", ":").split(":")
        d = int(parts[0]) if len(parts) > 0 and parts[0] else 0
        h = int(parts[1]) if len(parts) > 1 and parts[1] else 0
        m = int(parts[2]) if len(parts) > 2 and parts[2] else 0
        return d * 86400 + h * 3600 + m * 60

    # ── Tag Editor Tab ─────────────────────────────
    def _build_tag_editor_tab(self, parent):
        # Header with Reset button
        header = ctk.CTkFrame(parent, fg_color="transparent")
        header.pack(fill="x", pady=(2, 4))

        ctk.CTkLabel(
            header, text="Edit DICOM Tags",
            font=ctk.CTkFont(size=15, weight="bold"),
        ).pack(side="left")

        self.reset_btn = ctk.CTkButton(
            header, text="Reset All", command=self._reset_all_tags,
            width=90, height=28, fg_color="#7f8c8d", hover_color="#6c7a7d",
            font=ctk.CTkFont(size=12),
        )
        self.reset_btn.pack(side="right")

        ctk.CTkLabel(
            parent,
            text="Changed fields are highlighted in yellow. Original values shown for reference.",
            text_color=GRAY, font=ctk.CTkFont(size=11),
        ).pack(anchor="w", pady=(0, 6))

        # Scrollable tag form
        self.tag_scroll = ctk.CTkScrollableFrame(parent)
        self.tag_scroll.pack(fill="both", expand=True)
        self.tag_form = self.tag_scroll

        ctk.CTkLabel(
            self.tag_form, text="Load a DICOM folder to edit tags",
            text_color=GRAY,
        ).pack(pady=20)

        # SUV impact result box (below tag form)
        self.tag_result_frame = ctk.CTkFrame(parent, fg_color=DARK_BG, corner_radius=8)
        self.tag_result_frame.pack(fill="x", pady=(6, 0))
        self.tag_result_label = ctk.CTkLabel(
            self.tag_result_frame, text="",
            text_color=GRAY, font=ctk.CTkFont(size=13, family="Courier"),
            justify="left",
        )
        self.tag_result_label.pack(anchor="w", padx=14, pady=10)

    # ── Event Handlers ─────────────────────────────
    def _browse_folder(self):
        folder = filedialog.askdirectory(title="Select PET DICOM Folder")
        if not folder:
            return

        self.dicom_folder = Path(folder)
        display_path = str(self.dicom_folder)
        if len(display_path) > 60:
            display_path = "..." + display_path[-57:]
        self.folder_label.configure(text=display_path, text_color="white")

        try:
            files = find_dicom_files(self.dicom_folder)
            if not files:
                self.status_label.configure(text="No DICOM files!", text_color=RED)
                return

            self.dicom_info = read_dicom_info(self.dicom_folder)

            self.status_label.configure(text="Calculating SUVmax...", text_color=YELLOW)
            self.update_idletasks()
            self.suv_result = calculate_suvmax(self.dicom_folder)

            self.status_label.configure(
                text=f"{self.dicom_info['file_count']} files loaded", text_color=GREEN)
            self._populate_info()
            self._populate_tag_editor()
            self._update_correction_preview()

        except Exception as e:
            self.status_label.configure(text=f"Error: {e}", text_color=RED)

    def _populate_info(self):
        for w in self.info_frame.winfo_children():
            w.destroy()

        info = self.dicom_info
        if not info:
            return

        grid = ctk.CTkFrame(self.info_frame, fg_color="transparent")
        grid.pack(fill="x", padx=12, pady=10)
        grid.columnconfigure(1, weight=1)

        detected = info.get("detected_nuclide", "Unknown")

        # Half-life display
        hl = info["half_life_s"]
        if hl > 3600:
            hl_disp = f"{hl / 3600:.1f} h"
        elif hl > 60:
            hl_disp = f"{hl / 60:.1f} min"
        else:
            hl_disp = f"{hl:.0f} s"

        # Study date
        sd = info["study_date"]
        sd_disp = f"{sd[6:8]}.{sd[4:6]}.{sd[:4]}" if len(sd) == 8 else sd

        nuclide_text = f"{detected or 'Unknown'}  (Abundance: {info['positron_fraction']:.4%},  HL: {hl_disp})"
        is_mislabeled = (
            detected and detected != "Y-90"
            and "y90" in info.get("protocol_name", "").lower()
        )
        nuclide_color = RED if is_mislabeled else None

        dose_mci = info["injected_dose_bq"] / BQ_PER_MCI
        dose_mbq = info["injected_dose_bq"] / 1e6
        dose_text = f"{dose_mci:.2f} mCi  ({dose_mbq:.1f} MBq)" if dose_mbq > 0 else "MISSING"
        dose_color = RED if dose_mbq == 0 else None

        inj_text = info["injection_time"] or "MISSING"
        if inj_text != "MISSING" and len(inj_text) >= 6:
            clean = inj_text.split(".")[0]
            inj_text = f"{clean[:2]}:{clean[2:4]}:{clean[4:6]}"
        inj_color = RED if info["injection_time"] == "" else None

        rows = [
            ("Patient", f"{info['patient_name']}  ({info['patient_sex']}, {info['patient_weight']:.0f} kg)", None),
            ("Study", f"{sd_disp}  —  {info['study_description']}", None),
            ("Series", f"{info['series_description']}  ({info['reconstruction_method']})", None),
            ("Radionuclide", nuclide_text, nuclide_color),
            ("RescaleSlope", f"{info['rescale_slope']:.10g}", None),
            ("Decay Factor", f"{info['decay_factor']:.6f}  |  Frame: {info['frame_duration_ms'] / 1000:.0f} s", None),
            ("Injected Dose", dose_text, dose_color),
            ("Injection Time", inj_text, inj_color),
        ]

        # SUVmax
        if self.suv_result:
            suv = self.suv_result
            if suv["suvmax"] is not None:
                rows.append(("SUVmax", f"{suv['suvmax']:.2f}  (Max: {suv['max_bqml']:.1f} Bq/mL)", GREEN))
            else:
                rows.append(("SUVmax", f"N/A — {suv['error']}", YELLOW))

        for i, (label, value, color) in enumerate(rows):
            ctk.CTkLabel(
                grid, text=f"{label}:",
                font=ctk.CTkFont(size=12, weight="bold"),
                anchor="e", width=120,
            ).grid(row=i, column=0, sticky="e", padx=(0, 8), pady=1)

            ctk.CTkLabel(
                grid, text=str(value),
                font=ctk.CTkFont(size=12),
                text_color=color or "white",
                anchor="w",
            ).grid(row=i, column=1, sticky="w", pady=1)

        if detected:
            br_pct = info["positron_fraction"] * 100
            hl_s = info["half_life_s"]
            hl_str = f"{hl_s / 3600:.1f} h" if hl_s > 3600 else f"{hl_s / 60:.1f} min"
            self.current_nuclide_label.configure(
                text=f"DICOM says: {detected}  (Abundance: {br_pct:.4f}%,  HL: {hl_str})",
                text_color=YELLOW if detected != "Y-90" else GREEN,
            )

    def _populate_tag_editor(self):
        for w in self.tag_form.winfo_children():
            w.destroy()
        self.tag_entries.clear()
        self.original_formatted.clear()
        self.tag_dose_unit_var = ctk.StringVar(value="mCi")

        current = read_editable_tags(self.dicom_folder)

        # Configure grid columns: Field | Original | New Value + Controls
        self.tag_form.columnconfigure(0, weight=0, minsize=180)
        self.tag_form.columnconfigure(1, weight=0, minsize=150)
        self.tag_form.columnconfigure(2, weight=1)

        # Column headers
        for col, text in enumerate(["Field", "Original", "New Value"]):
            ctk.CTkLabel(
                self.tag_form, text=text,
                font=ctk.CTkFont(size=11, weight="bold"), text_color=MUTED,
            ).grid(row=0, column=col, sticky="w", padx=5, pady=(0, 5))

        row = 1
        all_tag_names = list(EDITABLE_TAGS.keys()) + list(EDITABLE_RADIO_TAGS.keys())

        for tag_name in all_tag_names:
            raw_val = current.get(tag_name, "")
            display_cfg = TAG_DISPLAY.get(tag_name, {})
            label = display_cfg.get("label", tag_name)
            unit = display_cfg.get("unit", "")
            fmt = display_cfg.get("fmt", "")

            formatted_val = format_tag_value(tag_name, raw_val)
            self.original_formatted[tag_name] = formatted_val

            # Column 0: Field label
            label_text = label + (f" ({unit})" if unit else "")
            ctk.CTkLabel(
                self.tag_form, text=label_text,
                font=ctk.CTkFont(size=12), anchor="w",
            ).grid(row=row, column=0, sticky="w", padx=5, pady=3)

            # Column 1: Original value (read-only)
            ctk.CTkLabel(
                self.tag_form, text=formatted_val or "—",
                font=ctk.CTkFont(size=12), text_color=GRAY, anchor="w",
            ).grid(row=row, column=1, sticky="w", padx=5, pady=3)

            # Column 2: New value + controls in a frame
            cell_frame = ctk.CTkFrame(self.tag_form, fg_color="transparent")
            cell_frame.grid(row=row, column=2, padx=5, pady=3, sticky="w")

            entry = ctk.CTkEntry(cell_frame, width=150, border_color=ENTRY_BORDER)
            if formatted_val:
                entry.insert(0, formatted_val)
            entry.pack(side="left")

            # Use sync callback for injection time/datetime fields
            if tag_name in ("RadiopharmaceuticalStartTime", "RadiopharmaceuticalStartDateTime"):
                on_change = lambda t=tag_name: self._on_datetime_change(t)
                entry.bind("<KeyRelease>", lambda e, t=tag_name: self._on_datetime_change(t))
                entry.bind("<FocusOut>", lambda e, t=tag_name: self._on_datetime_change(t))
            else:
                on_change = lambda t=tag_name: self._on_tag_change(t)
                entry.bind("<KeyRelease>", lambda e, t=tag_name: self._on_tag_change(t))
                entry.bind("<FocusOut>", lambda e, t=tag_name: self._on_tag_change(t))

            self.tag_entries[tag_name] = entry

            # Add controls based on field type
            if fmt == "time":
                _create_time_stepper(cell_frame, entry, on_change,
                                     include_day=False).pack(side="left", padx=(4, 0))
            elif fmt == "date":
                _create_date_stepper(cell_frame, entry, on_change).pack(
                    side="left", padx=(4, 0))
            elif fmt == "datetime":
                # Split datetime into date and time parts for steppers
                _create_date_stepper(cell_frame, entry,
                    lambda t=tag_name: self._on_datetime_change(t)).pack(
                    side="left", padx=(4, 0))
                _create_time_stepper(cell_frame, entry,
                    lambda t=tag_name: self._on_datetime_change(t),
                    include_day=False).pack(side="left", padx=(4, 0))
            elif fmt == "halflife":
                def _on_hl_select(choice, e=entry, t=tag_name):
                    hl_s = RADIONUCLIDES[choice]["half_life_s"]
                    e.delete(0, "end")
                    e.insert(0, format_tag_value("RadionuclideHalfLife", str(hl_s)))
                    self._on_tag_change(t)
                nuclide_names = list(RADIONUCLIDES.keys())
                ctk.CTkComboBox(
                    cell_frame, values=nuclide_names,
                    command=_on_hl_select, width=90,
                    state="readonly",
                ).pack(side="left", padx=(6, 0))
            elif fmt in ("dose_mci", "dose_mbq"):
                ctk.CTkSegmentedButton(
                    cell_frame, values=["mCi", "MBq"],
                    variable=self.tag_dose_unit_var,
                    command=lambda v, e=entry, t=tag_name: self._on_tag_dose_unit_change(v, e, t),
                    width=100,
                ).pack(side="left", padx=(6, 0))

            # Format hint
            hint = _format_hint(fmt)
            if hint:
                ctk.CTkLabel(cell_frame, text=hint, text_color=GRAY,
                             font=ctk.CTkFont(size=10)).pack(side="left", padx=(6, 0))

            row += 1

    def _on_datetime_change(self, changed_tag):
        """Sync Injection Time ↔ Injection DateTime time parts."""
        dt_entry = self.tag_entries.get("RadiopharmaceuticalStartDateTime")
        time_entry = self.tag_entries.get("RadiopharmaceuticalStartTime")
        if not dt_entry or not time_entry:
            self._on_tag_change(changed_tag)
            return

        if changed_tag == "RadiopharmaceuticalStartDateTime":
            # Extract time from datetime → write to injection time
            dt_text = dt_entry.get().strip()
            if " " in dt_text:
                time_part = dt_text.split(" ", 1)[1]
                time_entry.delete(0, "end")
                time_entry.insert(0, time_part)
                self._on_tag_change("RadiopharmaceuticalStartTime")
        elif changed_tag == "RadiopharmaceuticalStartTime":
            # Update time part in datetime
            dt_text = dt_entry.get().strip()
            new_time = time_entry.get().strip()
            if " " in dt_text:
                date_part = dt_text.split(" ", 1)[0]
                dt_entry.delete(0, "end")
                dt_entry.insert(0, f"{date_part} {new_time}")
            self._on_tag_change("RadiopharmaceuticalStartDateTime")

        self._on_tag_change(changed_tag)

    def _on_tag_dose_unit_change(self, new_unit, entry, tag_name):
        """Convert dose entry value when unit toggles between mCi and MBq."""
        try:
            val = float(entry.get())
            factor = 37.0 if new_unit == "MBq" else (1.0 / 37.0)
            entry.delete(0, "end")
            entry.insert(0, f"{val * factor:.2f}")
            self._on_tag_change(tag_name)
        except ValueError:
            pass

    def _on_tag_change(self, tag_name):
        """Highlight entry border yellow if value differs from original."""
        entry = self.tag_entries.get(tag_name)
        if not entry:
            return
        current = entry.get().strip()
        original = self.original_formatted.get(tag_name, "")
        if current != original:
            entry.configure(border_color=YELLOW, border_width=2)
        else:
            entry.configure(border_color=ENTRY_BORDER, border_width=2)
        self._update_tag_suv()

    def _parse_tag_time_seconds(self, time_str):
        """Parse HH:MM:SS string to total seconds from midnight."""
        parts = time_str.replace(".", ":").split(":")
        h = int(parts[0]) if len(parts) > 0 and parts[0] else 0
        m = int(parts[1]) if len(parts) > 1 and parts[1] else 0
        s = int(parts[2]) if len(parts) > 2 and parts[2] else 0
        return h * 3600 + m * 60 + s

    def _calc_tag_decay_time(self, use_new=False):
        """Calculate decay time in seconds from tag editor values (date-aware)."""
        # Try injection datetime first (has date + time)
        inj_dt_str = self._get_tag_val("RadiopharmaceuticalStartDateTime", use_new)
        scan_date_str = self._get_tag_val("AcquisitionDate", use_new)
        scan_time_str = self._get_tag_val("AcquisitionTime", use_new)
        inj_time_str = self._get_tag_val("RadiopharmaceuticalStartTime", use_new)

        try:
            # Parse injection date + time
            if inj_dt_str and " " in inj_dt_str:
                inj_date = _parse_date_str(inj_dt_str)
                inj_time_part = inj_dt_str.split(" ", 1)[1]
                inj_s = self._parse_tag_time_seconds(inj_time_part)
            elif inj_time_str and scan_date_str:
                # Use scan date as injection date (same day assumption)
                inj_date = _parse_date_str(scan_date_str)
                inj_s = self._parse_tag_time_seconds(inj_time_str)
            else:
                return 0

            # Parse scan date + time
            if scan_date_str and scan_time_str:
                scan_date = _parse_date_str(scan_date_str)
                scan_s = self._parse_tag_time_seconds(scan_time_str)
            else:
                return 0

            # Total seconds difference (date + time)
            day_diff = (scan_date - inj_date).days
            dt = day_diff * 86400 + (scan_s - inj_s)
            if dt < 0:
                dt += 86400
            return dt
        except (ValueError, IndexError):
            return 0

    def _get_tag_val(self, tag_name, use_new=False):
        """Get original or new formatted value for a tag."""
        if use_new:
            entry = self.tag_entries.get(tag_name)
            return entry.get().strip() if entry else ""
        return self.original_formatted.get(tag_name, "")

    def _update_tag_suv(self):
        """Update SUV impact display based on original vs new tag values."""
        if not hasattr(self, "tag_result_label") or not self.tag_entries:
            return

        try:
            # Parse weight
            orig_w = float(self._get_tag_val("PatientWeight", False))
            new_w = float(self._get_tag_val("PatientWeight", True))

            # Parse dose (always in mCi in display)
            orig_d_str = self._get_tag_val("RadionuclideTotalDose", False)
            new_d_str = self._get_tag_val("RadionuclideTotalDose", True)
            orig_d = float(orig_d_str) if orig_d_str else 0
            new_d = float(new_d_str) if new_d_str else 0
            # Convert to Bq
            unit = self.tag_dose_unit_var.get()
            if unit == "MBq":
                orig_d_bq = orig_d * 1_000_000
                new_d_bq = new_d * 1_000_000
            else:
                orig_d_bq = orig_d * BQ_PER_MCI
                new_d_bq = new_d * BQ_PER_MCI

            # Parse injection datetime and scan datetime to get decay time
            orig_dt = self._calc_tag_decay_time(False)
            new_dt = self._calc_tag_decay_time(True)

            # Parse half-life (format: "6586  (109.8 min)" → take first number)
            orig_hl_str = self._get_tag_val("RadionuclideHalfLife", False)
            new_hl_str = self._get_tag_val("RadionuclideHalfLife", True)
            orig_hl = float(orig_hl_str.split()[0]) if orig_hl_str else 0
            new_hl = float(new_hl_str.split()[0]) if new_hl_str else 0

            if orig_w <= 0 or new_w <= 0 or orig_d_bq <= 0 or new_d_bq <= 0:
                self.tag_result_label.configure(text="", text_color=GRAY)
                return
            if orig_hl <= 0 or new_hl <= 0 or orig_dt <= 0 or new_dt <= 0:
                self.tag_result_label.configure(text="", text_color=GRAY)
                return

            # SUV factors
            orig_decay = 2 ** (-orig_dt / orig_hl)
            new_decay = 2 ** (-new_dt / new_hl)
            orig_suv = (orig_w * 1000) / (orig_d_bq * orig_decay)
            new_suv = (new_w * 1000) / (new_d_bq * new_decay)

            suv_ratio = new_suv / orig_suv
            pct_total = (suv_ratio - 1) * 100

            # Individual effects
            pct_weight = (new_w / orig_w - 1) * 100
            pct_dose = (orig_d_bq / new_d_bq - 1) * 100
            pct_decay = (orig_decay / new_decay - 1) * 100

            fmt_pct = lambda v: f"+{v:.2f}%" if v >= 0 else f"{v:.2f}%"

            # Check if anything changed
            if abs(pct_total) < 0.005:
                self.tag_result_label.configure(
                    text="No SUV impact from current changes", text_color=GRAY)
                return

            lines = [
                "SUV impact of tag changes:",
                f"  Weight:      {orig_w:.1f} → {new_w:.1f} kg     {fmt_pct(pct_weight)}",
                f"  Dose:        {orig_d:.2f} → {new_d:.2f} {unit}    {fmt_pct(pct_dose)}",
                f"  Decay time:  {orig_dt/60:.0f} min → {new_dt/60:.0f} min  {fmt_pct(pct_decay)}",
                "",
                f"SUV change: {suv_ratio:.4f}x  ({fmt_pct(pct_total)})",
            ]

            self.tag_result_label.configure(
                text="\n".join(lines), text_color=YELLOW)

        except (ValueError, ZeroDivisionError, IndexError):
            self.tag_result_label.configure(text="", text_color=GRAY)

    def _reset_all_tags(self):
        """Reset all tag entries to their original DICOM values."""
        for tag_name, entry in self.tag_entries.items():
            original = self.original_formatted.get(tag_name, "")
            entry.delete(0, "end")
            if original:
                entry.insert(0, original)
            entry.configure(border_color=ENTRY_BORDER, border_width=2)

    def _update_correction_preview(self, _=None):
        # Update target info label (works without DICOM)
        target = self.target_nuclide_var.get()
        t_data = RADIONUCLIDES.get(target, {})
        t_hl = t_data.get("half_life_s", 0)
        t_hl_str = f"{t_hl / 3600:.1f} h" if t_hl > 3600 else f"{t_hl / 60:.1f} min"
        t_br = t_data.get("positron_br", 0) * 100
        self.target_info_label.configure(
            text=f"(Abundance: {t_br:.4f}%,  HL: {t_hl_str})")

        if not self.dicom_info:
            return

        source = self.dicom_info.get("detected_nuclide")

        if not source or source not in RADIONUCLIDES:
            self.correction_label.configure(
                text="Cannot detect current radionuclide from DICOM", text_color=RED)
            return

        if source == target:
            self.correction_label.configure(
                text="No correction needed — source and target are the same", text_color=GREEN)
            return

        factor = get_branching_ratio_correction(source, target)
        old_slope = self.dicom_info["rescale_slope"]
        new_slope = old_slope * factor

        source_data = RADIONUCLIDES[source]
        target_data = RADIONUCLIDES[target]
        old_hl = source_data["half_life_s"]
        new_hl = target_data["half_life_s"]
        old_hl_disp = f"{old_hl / 3600:.1f} h" if old_hl > 3600 else f"{old_hl / 60:.1f} min"
        new_hl_disp = f"{new_hl / 3600:.1f} h" if new_hl > 3600 else f"{new_hl / 60:.1f} min"

        fmt_pct = lambda v: f"+{v:.2f}%" if v >= 0 else f"{v:.2f}%"
        pct_abund = (factor - 1) * 100

        lines = [
            f"Correction:  {source} → {target}",
            "",
            f"  Abundance:    {source_data['positron_br']:.6%} → {target_data['positron_br']:.6%}",
            f"  RescaleSlope: {old_slope:.10g} → {new_slope:.6g}",
            f"  Half-Life:    {old_hl_disp} → {new_hl_disp}",
            "",
            f"Abundance correction: {factor:.4f}x  ({fmt_pct(pct_abund)})",
        ]

        # SUV impact using DICOM data
        decay_time_s = self.dicom_info.get("decay_time_s", 0)
        if decay_time_s > 0:
            old_decay = 2 ** (-decay_time_s / old_hl)
            new_decay = 2 ** (-decay_time_s / new_hl)
            pct_decay = (old_decay / new_decay - 1) * 100
            dt_min = decay_time_s / 60
            lines.append(f"Decay effect (HL change, {dt_min:.0f} min): {fmt_pct(pct_decay)}")
            lines.append("")
            suv_ratio = factor * (old_decay / new_decay)
            pct_total = (suv_ratio - 1) * 100
            lines.append(f"{factor:.4f} × {old_decay / new_decay:.4f} = {suv_ratio:.4f}x  ({fmt_pct(pct_total)})")
        else:
            suv_ratio = factor
            pct_total = pct_abund

        # SUVmax examples
        if self.suv_result and self.suv_result["max_bqml"] > 0:
            old_max = self.suv_result["max_bqml"]
            new_max = old_max * suv_ratio
            lines.append("")
            lines.append(f"Max Bq/mL:   {old_max:.2f}  →  {new_max:,.2f}")
            if self.suv_result["suvmax"] is not None:
                old_suv = self.suv_result["suvmax"]
                new_suv = old_suv * suv_ratio
                lines.append(f"SUVmax:      {old_suv:.2f}  →  {new_suv:,.2f}")
            else:
                lines.append(f"SUVmax:      N/A — {self.suv_result['error']}")

        text = "\n".join(lines)
        color = YELLOW
        if factor > 100:
            color = RED
            text += "\n\nLarge correction factor — verify radionuclide!"

        self.correction_label.configure(text=text, text_color=color)

    def _collect_tag_changes(self) -> dict:
        """Collect only changed tag values, converting back to DICOM format."""
        changes = {}
        dose_unit = getattr(self, "tag_dose_unit_var", None)
        for tag_name, entry in self.tag_entries.items():
            current_val = entry.get().strip()
            original_val = self.original_formatted.get(tag_name, "")
            if current_val != original_val and current_val != "":
                # If dose is in MBq, use dose_mbq parser
                if tag_name == "RadionuclideTotalDose" and dose_unit and dose_unit.get() == "MBq":
                    # MBq → Bq directly
                    try:
                        dicom_val = str(float(current_val) * 1_000_000)
                    except ValueError:
                        dicom_val = current_val
                else:
                    dicom_val = parse_tag_input(tag_name, current_val)
                changes[tag_name] = dicom_val
        return changes

    def _get_nuclide_correction(self) -> dict | None:
        if not self.apply_nuclide_var.get() or not self.dicom_info:
            return None
        source = self.dicom_info.get("detected_nuclide")
        target = self.target_nuclide_var.get()
        if not source or source == target:
            return None
        return {
            "source": source,
            "target": target,
        }

    def _preview_changes(self):
        if not self.dicom_folder or not self.dicom_info:
            messagebox.showwarning("Warning", "Load a DICOM folder first.")
            return

        tag_changes = self._collect_tag_changes()
        nuclide_corr = self._get_nuclide_correction()

        if not tag_changes and not nuclide_corr:
            messagebox.showinfo("Preview", "No changes to apply.")
            return

        lines = ["Changes to apply:\n"]

        if nuclide_corr:
            source, target = nuclide_corr["source"], nuclide_corr["target"]
            factor = get_branching_ratio_correction(source, target)
            lines.append(f"RADIONUCLIDE CORRECTION: {source} -> {target}")
            lines.append(f"  Abundance correction factor: {factor:,.2f}x")
            lines.append(f"  Half-Life: -> {RADIONUCLIDES[target]['half_life_s']:.0f} s")
            lines.append("")

        if tag_changes:
            lines.append("TAG EDITS:")
            for tag_name, dicom_val in tag_changes.items():
                display_cfg = TAG_DISPLAY.get(tag_name, {})
                label = display_cfg.get("label", tag_name)
                user_val = self.tag_entries[tag_name].get().strip()
                orig = self.original_formatted.get(tag_name, "")
                lines.append(f"  {label}: {orig}  ->  {user_val}")

        lines.append(f"\nFiles: {self.dicom_info['file_count']}")
        lines.append(f"Output: {self.dicom_folder.name}_corrected/")
        lines.append("\nOriginal files will NOT be modified.")

        messagebox.showinfo("Preview", "\n".join(lines))

    def _apply_and_save(self):
        if not self.dicom_folder or not self.dicom_info:
            messagebox.showwarning("Warning", "Load a DICOM folder first.")
            return

        tag_changes = self._collect_tag_changes()
        nuclide_corr = self._get_nuclide_correction()

        if not tag_changes and not nuclide_corr:
            messagebox.showinfo("Info", "No changes to apply.")
            return

        msg = "Apply corrections?\n\n"
        if nuclide_corr:
            factor = get_branching_ratio_correction(
                nuclide_corr["source"], nuclide_corr["target"]
            )
            msg += f"Radionuclide: {nuclide_corr['source']} -> {nuclide_corr['target']} (x{factor:,.0f})\n"
        if tag_changes:
            msg += f"Tag edits: {len(tag_changes)} fields\n"
        msg += f"\nFiles: {self.dicom_info['file_count']}\n"
        msg += "Original files will NOT be modified."

        if not messagebox.askyesno("Confirm", msg):
            return

        output_folder = self.dicom_folder.parent / f"{self.dicom_folder.name}_corrected"

        if output_folder.exists():
            if not messagebox.askyesno(
                "Warning",
                f"Output folder already exists:\n{output_folder}\n\nOverwrite?",
            ):
                return

        try:
            count = apply_corrections(
                self.dicom_folder,
                output_folder,
                tag_changes=tag_changes if tag_changes else None,
                nuclide_correction=nuclide_corr,
            )
            messagebox.showinfo(
                "Success",
                f"{count} DICOM files corrected!\n\n"
                f"Saved to:\n{output_folder}\n\n"
                f"Backup: _correction_backup.json",
            )
        except Exception as e:
            messagebox.showerror("Error", f"Correction failed:\n{e}")


def _format_hint(fmt: str) -> str:
    """Return a format hint string for the tag editor."""
    return {
        "date": "DD.MM.YYYY",
        "time": "HH:MM:SS",
        "datetime": "DD.MM.YYYY HH:MM:SS",
        "dose_mbq": "MBq",
        "dose_mci": "mCi",
        "sex": "M / F",
        "float1": "e.g. 75.0",
        "float2": "e.g. 1.75",
        "halflife": "seconds",
    }.get(fmt, "")


# ── Reusable widget helpers ──────────────────────

BTN_W = 28
BTN_H = 24
BTN_FG = "#3a3a4a"
BTN_HOVER = "#4a4a5a"

_btn_font_cache = None

def _btn_font():
    global _btn_font_cache
    if _btn_font_cache is None:
        _btn_font_cache = ctk.CTkFont(size=10)
    return _btn_font_cache


def _step_time(entry, field, delta, has_day=False):
    """Increment/decrement a time entry field (D, H, or M) by delta.
    Works with 'DD:HH:MM', 'HH:MM:SS', and 'DD.MM.YYYY HH:MM:SS' formats."""
    text = entry.get().strip()

    # Handle datetime format: step only the time part
    date_prefix = ""
    time_text = text
    if " " in text:
        date_prefix = text.split(" ", 1)[0] + " "
        time_text = text.split(" ", 1)[1]

    parts = time_text.replace(".", ":").split(":")

    if has_day:
        # DD:HH:MM format
        d = int(parts[0]) if len(parts) > 0 and parts[0] else 0
        h = int(parts[1]) if len(parts) > 1 and parts[1] else 0
        m = int(parts[2]) if len(parts) > 2 and parts[2] else 0
        if field == "D":
            d = max(0, min(30, d + delta))
        elif field == "H":
            h = (h + delta) % 24
        elif field == "M":
            m = (m + delta) % 60
        new_time = f"{d:02d}:{h:02d}:{m:02d}"
    else:
        # HH:MM:SS format
        h = int(parts[0]) if len(parts) > 0 and parts[0] else 0
        m = int(parts[1]) if len(parts) > 1 and parts[1] else 0
        s = int(parts[2]) if len(parts) > 2 and parts[2] else 0
        if field == "H":
            h = (h + delta) % 24
        elif field == "M":
            m = (m + delta) % 60
        new_time = f"{h:02d}:{m:02d}:{s:02d}"

    entry.delete(0, "end")
    entry.insert(0, f"{date_prefix}{new_time}")


def _create_time_stepper(parent, entry, callback, include_day=False):
    """Add D+/D-/H+/H-/M+/M- buttons next to a time entry."""
    frame = ctk.CTkFrame(parent, fg_color="transparent")

    def make_btn(text, field, delta):
        return ctk.CTkButton(
            frame, text=text, width=BTN_W, height=BTN_H,
            fg_color=BTN_FG, hover_color=BTN_HOVER, font=_btn_font(),
            command=lambda: (_step_time(entry, field, delta, include_day), callback()),
        )

    if include_day:
        make_btn("D+", "D", 1).pack(side="left", padx=1)
        make_btn("D-", "D", -1).pack(side="left", padx=(1, 4))

    make_btn("H+", "H", 1).pack(side="left", padx=1)
    make_btn("H-", "H", -1).pack(side="left", padx=(1, 4))
    make_btn("M+", "M", 1).pack(side="left", padx=1)
    make_btn("M-", "M", -1).pack(side="left", padx=1)

    return frame


def _parse_date_str(text):
    """Parse DD.MM.YYYY from text (may contain time after space)."""
    from datetime import date
    date_part = text.split()[0] if " " in text else text
    parts = date_part.replace("/", ".").replace("-", ".").split(".")
    d = int(parts[0]) if len(parts) > 0 and parts[0] else 1
    m = int(parts[1]) if len(parts) > 1 and parts[1] else 1
    y = int(parts[2]) if len(parts) > 2 and parts[2] else 2025
    return date(y, m, d)


def _step_date(entry, field, delta):
    """Increment/decrement a date entry field (D, M, or Y) by delta.
    Works with both 'DD.MM.YYYY' and 'DD.MM.YYYY HH:MM:SS' formats."""
    from datetime import date, timedelta
    text = entry.get().strip()
    # Separate time part if present (datetime format)
    time_suffix = ""
    if " " in text:
        time_suffix = " " + text.split(" ", 1)[1]

    try:
        dt = _parse_date_str(text)
    except (ValueError, IndexError):
        return

    if field == "D":
        dt += timedelta(days=delta)
    elif field == "M":
        m_new = dt.month + delta
        y_new = dt.year
        while m_new > 12:
            m_new -= 12
            y_new += 1
        while m_new < 1:
            m_new += 12
            y_new -= 1
        day = min(dt.day, 28)  # safe for all months
        dt = date(y_new, m_new, day)
    elif field == "Y":
        try:
            dt = date(dt.year + delta, dt.month, min(dt.day, 28))
        except ValueError:
            return

    entry.delete(0, "end")
    entry.insert(0, f"{dt.day:02d}.{dt.month:02d}.{dt.year}{time_suffix}")


def _create_date_stepper(parent, entry, callback):
    """Add D+/D-/M+/M-/Y+/Y- buttons next to a date entry."""
    frame = ctk.CTkFrame(parent, fg_color="transparent")

    def make_btn(text, field, delta):
        return ctk.CTkButton(
            frame, text=text, width=BTN_W, height=BTN_H,
            fg_color=BTN_FG, hover_color=BTN_HOVER, font=_btn_font(),
            command=lambda: (_step_date(entry, field, delta), callback()),
        )

    make_btn("D+", "D", 1).pack(side="left", padx=1)
    make_btn("D-", "D", -1).pack(side="left", padx=(1, 4))
    make_btn("M+", "M", 1).pack(side="left", padx=1)
    make_btn("M-", "M", -1).pack(side="left", padx=(1, 4))
    make_btn("Y+", "Y", 1).pack(side="left", padx=1)
    make_btn("Y-", "Y", -1).pack(side="left", padx=1)

    return frame


def _step_pct(entry, factor):
    """Multiply entry value by factor (e.g. 1.1 for +10%)."""
    try:
        val = float(entry.get())
        new_val = val * factor
        entry.delete(0, "end")
        entry.insert(0, f"{new_val:.1f}")
    except ValueError:
        pass


def _create_pct_buttons(parent, entry, callback):
    """Add -10% / +10% buttons next to a numeric entry."""
    frame = ctk.CTkFrame(parent, fg_color="transparent")

    ctk.CTkButton(
        frame, text="-10%", width=40, height=BTN_H,
        fg_color=BTN_FG, hover_color=BTN_HOVER, font=_btn_font(),
        command=lambda: (_step_pct(entry, 0.9), callback()),
    ).pack(side="left", padx=1)

    ctk.CTkButton(
        frame, text="+10%", width=40, height=BTN_H,
        fg_color=BTN_FG, hover_color=BTN_HOVER, font=_btn_font(),
        command=lambda: (_step_pct(entry, 1.1), callback()),
    ).pack(side="left", padx=1)

    return frame
