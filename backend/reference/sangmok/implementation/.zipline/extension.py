import os
from pathlib import Path

from zipline.data.bundles import register
from zipline.data.bundles.csvdir import csvdir_equities

csv_dir = Path(os.environ.get("KRX_CSV_DIR", "data/zipline_csvdir")).resolve()

register(
    "krx-csvdir",
    csvdir_equities(["daily"], str(csv_dir)),
    calendar_name="XKRX",
)
