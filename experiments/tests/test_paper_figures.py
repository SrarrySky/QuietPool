import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pytest
from PIL import Image

from experiments.analysis.paper_figures import save_figure, timing_cost_figure


def test_save_figure_writes_vector_and_review_formats(tmp_path):
    fig, ax = plt.subplots()
    ax.plot([0, 1], [0, 1])
    save_figure(fig, tmp_path, "publication")

    assert {path.suffix for path in tmp_path.iterdir()} == {".pdf", ".svg", ".png"}
    assert all(path.stat().st_size > 0 for path in tmp_path.iterdir())
    with Image.open(tmp_path / "publication.png") as image:
        assert image.info["dpi"] == pytest.approx((600, 600), abs=1)


def test_timing_cost_scale_keeps_single_holding_reference_visible():
    rows = []
    for indicator in ("e", "g2"):
        for view in ("V0", "V1"):
            for volatility, delta in ((3.0e-4, 0.0), (3.7e-4, 0.2)):
                rows.append(
                    {
                        "indicator": indicator,
                        "view": view,
                        "threshold_pct": 95,
                        "volatility": volatility,
                        "delta_c_bps": delta if indicator == "e" else delta + 100,
                        "prediction_bps": 10.0 if indicator == "e" else np.nan,
                    }
                )
    fig = timing_cost_figure(pd.DataFrame(rows), {"delta_bps": 2.5}, "e", "V0")

    assert fig.axes[0].get_ylim()[1] > 10.0
    assert len(fig.axes) == 1
    plt.close(fig)
