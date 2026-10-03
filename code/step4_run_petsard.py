"""Step 4 (inside the TTP): run PETsARD on the wide table and collect the synthetic data
and the evaluation report into petsard_output/."""
import os
import shutil
from pathlib import Path

from petsard import Executor

BASE = Path(__file__).resolve().parent
OUT = BASE / "petsard_output"


def main():
    os.chdir(BASE)
    before = set(BASE.glob("petsard*.csv"))
    Executor(config=str(BASE / "petsard_config.yaml")).run()
    OUT.mkdir(exist_ok=True)
    for f in set(BASE.glob("petsard*.csv")) - before:
        shutil.move(str(f), OUT / f.name)
    for f in sorted(OUT.glob("*.csv")):
        print("->", f.relative_to(BASE))


if __name__ == "__main__":
    main()
