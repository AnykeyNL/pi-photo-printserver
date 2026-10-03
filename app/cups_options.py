"""Map UI print settings to Gutenprint/CUPS lp options."""

# Gutenprint PPD PageSize tokens for Sony UP-DR200 (334dpi internal).
MEDIA_PAGE_SIZE: dict[str, str] = {
    "4x6": "w288h432",
    "5x7": "w360h504",
    "6x8": "w432h576",
}


def lp_options_for_job(media: str, orientation: str) -> list[str]:
    """Build -o options so CUPS runs the Gutenprint filter (not raw JPEG to USB backend)."""
    page = MEDIA_PAGE_SIZE.get(media, "w288h432")
    # Landscape JPEG is wide×tall at 334dpi (see imaging._cups_compose_dimensions).
    # orientation-requested=4 maps it to the 4×6 feed; do not also rotate pixels in Pillow.
    orient_opt = (
        "orientation-requested=4"
        if orientation == "landscape"
        else "orientation-requested=3"
    )
    return [
        f"PageSize={page}",
        orient_opt,
        "StpiShrinkOutput=Shrink",
        "StpImageType=Photo",
        "StpColorCorrection=None",
        "Resolution=334dpi",
    ]
