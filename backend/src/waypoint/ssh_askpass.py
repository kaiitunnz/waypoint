#!/usr/bin/env python3
"""SSH_ASKPASS helper for password-authenticated launch targets.

OpenSSH invokes this program (with the prompt text as ``argv[1]``) to obtain a
password non-interactively when ``SSH_ASKPASS`` points at it and
``SSH_ASKPASS_REQUIRE=force`` is set. It carries no secret of its own — it only
echoes the password the parent process placed in the environment for the
lifetime of a single ``ssh`` invocation. See ``ssh_master.py``.
"""

import os
import sys

PASSWORD_ENV_VAR = "WAYPOINT_SSH_PASSWORD"


def main() -> None:
    sys.stdout.write(os.environ.get(PASSWORD_ENV_VAR, ""))


if __name__ == "__main__":
    main()
