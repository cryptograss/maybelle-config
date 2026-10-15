"""python3 -m unittest hunter/scripts/test_build_timing.py"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from build_timing import report, steps  # noqa: E402

LOG = """#1 [internal] load build definition from Dockerfile
#1 DONE 0.0s
#5 [ 2/40] RUN apt-get update && apt-get install -y tini
#5 CACHED
#6 [ 3/40] COPY packages.txt /tmp/
#6 CACHED
#9 [ 9/40] RUN npx playwright install-deps
#9 0.512 Installing dependencies...
#9 DONE 612.4s
#12 [21/40] RUN cd /home/magent/workspace/arthel && npm install
#12 DONE 95.0s
#14 [25/40] RUN git clone https://example/magenta
#14 ERROR: process did not complete
#15 [26/40] RUN slow thing
"""


class BuildTimingTest(unittest.TestCase):

    def test_steps_their_times_and_the_cache(self):
        found = steps(LOG.splitlines())
        self.assertEqual(found[1], ('[2/40] RUN apt-get update && apt-get install -y tini', None, 'cached'))
        self.assertEqual(found[3], ('[9/40] RUN npx playwright install-deps', 612.4, 'ran'))
        self.assertEqual([s[2] for s in found[-2:]], ['error', 'unfinished'])

    def test_the_report_puts_the_slowest_first_and_says_where_the_cache_stopped(self):
        text = report(steps(LOG.splitlines()))
        self.assertIn('7 steps: 3 ran (707s), 2 cached, 1 error, 1 unfinished', text)
        lines = text.splitlines()
        self.assertIn('612.4s  [9/40] RUN npx playwright install-deps', lines[1])
        self.assertIn('cache stopped at: [9/40] RUN npx playwright install-deps', text)


if __name__ == '__main__':
    unittest.main()
