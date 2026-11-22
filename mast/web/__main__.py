# -*- coding: utf-8 -*-
# This file is part of McIndi's Automated Solutions Tool (MAST).
#
# MAST is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 3
# as published by the Free Software Foundation.
#
# MAST is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with MAST.  If not, see <https://www.gnu.org/licenses/>.
#
# Copyright 2015-2026, McIndi Solutions, All rights reserved.
import sys

from mast.logging import make_logger
from mast.web.server import StartupError, main

logger = make_logger("mast.datapower.web")
logger.info("Attempting to start web gui.")
try:
    main()
except StartupError as e:
    logger.error(str(e))
    print("mast-web: {}".format(e), file=sys.stderr)
    sys.exit(1)
logger.info("web gui stopped")
