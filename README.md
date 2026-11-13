# mast

McIndi's Automated Solutions Tool (mast) for IBM DataPower is a powerful and
fully featured suite of tools with a focus on multi-box management strategies
for environments with many IBM DataPower appliances. Some of the more useful
automations are:

    * Backups
    * Checkpoints
	* Adding/Removing users
	* Forcing password changes
	* Exports
    * Imports
	* Adding/Removing Domains
	* Deployments
	* TCP Connection tests
	* Routing Table
	* Network changes
	    * Secondary IP addresses
	    * Host Aliases
	    * DNS Static Hosts
    * And much, much more

As well as many other advanced tools:

    * multi-box ssh client
    * Automated PMR information gathering
    * Configuration Change Tracker

Mast is Open Source and released under the GPLv3. If you are interested in
obtaining commercial support or a commercial license, please email us at
sales@mcindi.com.

# links

* [organization dashboard](https://github.com/orgs/McIndi/dashboard)
* [current projects](https://github.com/McIndi/mast3/projects)
* [issue tracker](https://github.com/McIndi/mast3/issues)
* [pull requests](https://github.com/mcindi/mast3/pulls)

# tests

The tests run with [pytest](https://docs.pytest.org) from a source checkout:

    python -m venv .venv
    .venv/bin/pip install -r linux-requirements.txt pytest dulwich pandas
    .venv/bin/pip install -e .
    .venv/bin/python -m pytest mast/testsuite/unit mast/testsuite/regression

The integration tests start a real IBM DataPower Gateway in a container,
with podman or docker. IBM publishes "limited" images for non-production
use. The image does not start until you accept the IBM license, so you
must set a variable to run these tests:

    export MAST_TEST_DATAPOWER_ACCEPT_LICENSE=true
    .venv/bin/python -m pytest mast/testsuite/integration

Without that variable, or without a container runtime, the integration
tests are skipped. To test a different firmware version, set
`MAST_TEST_DATAPOWER_IMAGE`, for example to
`icr.io/cpopen/datapower/datapower-limited:10.6.0.11`. More options are
described in `mast/testsuite/integration/container.py`.
