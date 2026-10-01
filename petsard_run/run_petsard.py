import sys

from petsard import Executor

config = sys.argv[1] if len(sys.argv) > 1 else "petsard_config_A_trial.yaml"
exec = Executor(config=config)
exec.run()
