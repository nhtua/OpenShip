"""Offline functional contract for the logs/data image, at workload limits."""

import csv
import io
import json
import math
import re
import sys
import tempfile
import uuid
from decimal import Decimal
from importlib.metadata import version
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd
import regex
from drain3 import TemplateMiner
from drain3.template_miner_config import TemplateMinerConfig
from PIL import Image
from pygrok import Grok

assert sys.version_info >= (3, 12)
assert Decimal("0.1") + Decimal("0.2") == Decimal("0.3")
assert math.isqrt(49) == 7 and uuid.UUID(str(uuid.uuid4())).version == 4
assert re.fullmatch(r"service=\w+", "service=api")
text = "service,count\napi,2\napi,3\nworker,7\n"
assert len(list(csv.DictReader(io.StringIO(text)))) == 3
data = pd.read_csv(io.StringIO(text))
grouped = data.groupby("service")["count"].sum().to_dict()
assert grouped == {"api": 5, "worker": 7}
assert json.loads(json.dumps(grouped)) == grouped
assert int(np.sum(data["count"])) == 12
assert regex.search(r"service=(?P<service>\w+)", "service=api")["service"] == "api"
assert Grok("%{IP:ip} %{NUMBER:status}").match("192.0.2.1 200") == {"ip": "192.0.2.1", "status": "200"}
config = TemplateMinerConfig()
config.drain_sim_th = 0.6
miner = TemplateMiner(config=config)
ids = [miner.add_log_message(message)["cluster_id"] for message in (
    "request served path 100", "request served path 200",
    "disk failure sector 300", "disk failure sector 400",
)]
assert ids[0] == ids[1] and ids[2] == ids[3] and ids[0] != ids[2]
assert len(miner.drain.clusters) == 2
matplotlib.use("Agg")
from matplotlib import pyplot as plt

with tempfile.TemporaryDirectory() as directory:
    chart = Path(directory) / "chart.png"
    fig, ax = plt.subplots()
    ax.bar(list(grouped), list(grouped.values()))
    fig.savefig(chart)
    plt.close(fig)
    assert chart.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    with Image.open(chart) as decoded:
        decoded.load()
        assert decoded.width > 0 and decoded.height > 0
print(json.dumps({"profile": "logs-data", "groups": grouped, "clusters": 2, "png": "decoded",
                  "versions": {name: version(name) for name in (
                      "pandas", "numpy", "matplotlib", "regex", "pygrok", "drain3")}}))
