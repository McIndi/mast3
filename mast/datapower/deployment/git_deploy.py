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
# Copyright 2015-2024, McIndi Solutions, All rights reserved.

from mast.datapower import datapower
from mast.plugin_utils.params import ApplianceOptions, OUT_DIR
from mast.plugin_utils.results import Report, StatusResults
from collections import Counter
from mast.logging import make_logger
from mast.config import get_config
from mast.xor import xorencode, xordecode
from mast.util import _s, _b
from subprocess import Popen
from mast.timestamp import Timestamp
from mast.cli import Cli, Option, say
from urllib.parse import urlparse, urlunparse
from urllib.parse import quote_plus
from time import sleep, time
from functools import partial
from collections import OrderedDict, defaultdict
import xml.etree.cElementTree as etree
from zipfile import ZipFile
from os.path import exists
import ntpath
import binascii
import logging
import subprocess
import shutil
import os
import sys
import contextlib
import sys
import multiprocessing
from multiprocessing import Process
from multiprocessing.queues import Queue
from threading import Thread
import errno
import stat

def handleRemoveReadonly(func, path, exc):
  excvalue = exc[1]
  if func in (os.rmdir, os.remove) and excvalue.errno == errno.EACCES:
      os.chmod(path, stat.S_IRWXU| stat.S_IRWXG| stat.S_IRWXO) # 0777
      func(path)
  else:
      raise

# The queue of the progress window, if there is one. See StdoutDisplay.
_window = None


def print(msg):
    """Report progress: to the log, to the user and to the progress
    window if there is one."""
    log = make_logger("mast.datapower.deployment.results")
    if msg is not None:
        msg = _s(msg)
        log.info(msg)
        say(msg.rstrip())
        if _window is not None:
            _window.put("{}{}".format(msg.rstrip(), os.linesep))


class StdoutDisplay(Process):
    def __init__(self, q, *args, **kwargs):
        Process.__init__(self, *args, **kwargs)
        self.q = q

    def close(self):
#        self.q.put("END")
        self.gui_root.quit()

    def run(self):
        # tkinter is imported here, and not at the top of the module,
        # because a Python on a server often has no tkinter. Only the
        # output window needs it.
        import tkinter
        self.gui_root = tkinter.Tk()
        self.gui_root.title("MAST Git-Deployment Output")
        self.gui_txt = tkinter.Text(self.gui_root)
        self.gui_txt.pack(fill="both", expand=True)
        # self.gui_btn = Button(self.gui_root, text='Close', command=self.close)
        # self.gui_btn.pack()

        # Instantiate and start the text monitor
        self.monitor = Thread(target=text_catcher,args=(self, self.gui_txt, self.q))
        self.monitor.daemon = True
        self.monitor.start()

        self.gui_root.mainloop()

# This function takes the text widget and a queue as inputs.
# It functions by waiting on new data entering the queue, when it
# finds new data it will insert it into the text widget
def text_catcher(parent, text_widget, queue):
    from tkinter import END
    while True:
        msg = queue.get()
        if msg.strip() == "END":
            break
        elif msg.strip() == "CLEAR":
            sleep(1)
            text_widget.delete("0.0", END)
            continue
        text_widget.insert(END, msg)
        text_widget.see(END)
    parent.close()

# This is a Queue that behaves like stdout
class StdoutQueue(Queue):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, ctx=multiprocessing.get_context(), **kwargs)

    def write(self,msg):
        self.put(msg)

    def flush(self):
        sys.__stdout__.flush()

@contextlib.contextmanager
def working_directory(path):
    """A context manager which changes the working directory to the given
    path, and then changes it back to its previous value on exit.
    """
    prev_cwd = os.getcwd()
    os.chdir(path)
    yield
    os.chdir(prev_cwd)

def system_call(
        command,
        stdin=None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=True,
    ):
    """
    # system_call

    helper function to shell out commands. This should be platform
    agnostic.
    """
    pipe = subprocess.Popen(
        command,
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        shell=shell,
    )
    stdout, stderr = pipe.communicate()
    if pipe.returncode > 0:
        raise RuntimeError("\n\nError calling git:\n{}\n{}".format(stderr, stdout))
    return stdout, stderr

def quit_for_local_uploads(config_dir):
    for filename in [x for x in os.listdir(config_dir) if "EMPTY" not in x.upper()]:
        _filename = os.path.join(config_dir, filename)
        tree = etree.parse(_filename)
        files = tree.findall(r'.//files/file')
        local_files = [
            f.get("name") for f in files if f.get("name").startswith("local:")
        ]
        if local_files:
            raise ValueError(
                "configuration '{}' contains files meant to "
                "be uploaded to 'local', this is not allowed. "
                "Use the allow_files_in_export option to perform "
                "this deployment anyway. ".format(
                    filename
                )
            )

DATAPOWER_SERVICE_TYPES = [
    "MultiProtocolGateway",
    "WSGateway",
    "B2BGateway",
    "XMLFirewallService",
    "WebAppFW",
    "WebTokenService",
    "XSLProxyService",
    "HTTPService",
    "TCPProxyService",
    "SSLProxyService",
    "APIGateway",
]

def wait_for_quiesce(appliance, app_domain, services, timeout):
    start = time()
    while True:
        sleep(5)
        done = []
        object_status = appliance.get_status("ObjectStatus", domain=app_domain)
        for service in services:
            _class = service["type"]
            name = service["name"]
            status = list(
                filter(
                    lambda _status: _status.findtext("Class") == _class and _status.findtext("Name") == name,
                    object_status.xml.findall(".//ObjectStatus"),
                )
            )
            if not len(status):
                # Service Does not exist
                done.append(True)
                continue
            status = status[0]
            if status.findtext("ErrorCode") == "in quiescence":
                done.append(True)
            else:
                done.append(False)
        if all(done):
            break
        if time() - start > timeout:
            break

def wait_for_unquiesce(appliance, app_domain, services, timeout):
    start = time()
    while True:
        sleep(5)
        done = []
        object_status = appliance.get_status("ObjectStatus", domain=app_domain)
        for service in services:
            _class = service["type"]
            name = service["name"]
            status = list(
                filter(
                    lambda _status: _status.findtext("Class") == _class and _status.findtext("Name") == name,
                    object_status.xml.findall(".//ObjectStatus"),
                )
            )
            status = status[0]
            if status.findtext("ErrorCode") != "in quiescence":
                done.append(True)
            else:
                done.append(False)
        if all(done):
            break
        if time() - start > timeout:
            break


class Plan(object):
    """A class representing a plan of action for the deployment.
    """
    def __init__(self, config):
        self.config = config
        if "subdirectory" in self.config:
            self.config["repo_dir"] = os.path.join(self.config["repo_dir"], self.config["subdirectory"])
        self.environment = config["_environment"]
        self.service = config["service"]
        self.deployment_policy = None

        # find the list of steps that need to be followed for each appliance
        self._uploads = self._find_uploads()
        self._merge_deployment_policies()
        self._imports = self._find_imports()
        self._services = self._find_services()
        self._password_map_aliases = self._find_password_map_aliases()

        # find the list of steps that need to be followed for all appliances
        self._actions = self._plan()

        # print(str(self._actions))
        with open(os.path.join(config["out_dir"], "plan.txt"), "w") as fp:
            for index, action in enumerate(self):
                print(repr(action))
                fp.write("Step {}, {}{}".format(index, action.name, os.linesep))
                for k, v in list(action.kwargs.items()):
                    fp.write("\t{}={}{}".format(k, v, os.linesep))

    def __iter__(self):
        for action in self._actions:
            yield action

    def list_steps(self):
        output = OrderedDict()
        output["App Domains"] = ""
        for appliance in self.environment.appliances:
            app_domain = self.config["domains"][self.config["appliances"].index(appliance.hostname)]
            output["App Domains"] += "{} -> {}\n".format(appliance.hostname, app_domain)
        output["Merged Deployment Policies"] = ""
        for filename in self._merged_deployment_policies:
            output["Merged Deployment Policies"] += "{}\n".format(filename)
        output["Services"] = ""
        for kwargs in self._services:
            output["Services"] += "{}: {}\n".format(kwargs["type"], kwargs["name"])
        output["Directories to Create"] = ""
        for hostname, dirs in list(self.dirs_to_create.items()):
            if dirs:
                output["Directories to Create"] = "{}{}".format(hostname, os.linesep)
                for directory in dirs:
                    output["Directories to Create"] = "\t{}{}".format(directory, os.linesep)
        output["Uploads"] = ""
        for filename, kwargs in list(self._uploads.items()):
            output["Uploads"] += "{} -> {}{}".format(os.path.relpath(kwargs["file_in"], self.config["repo_dir"]), kwargs["file_out"], os.linesep)
        if self.deployment_policy is None:
            output["Imports"] = ""
        else:
            output["Imports"] = "{}{}".format("merged_deployment_policy.xcfg", os.linesep)
        for kwargs in self._imports:
            output["Imports"] += "{}{}".format(os.path.relpath(kwargs["zip_file"], self.config["repo_dir"]), os.linesep)
        output["Password Map Aliases"] = ""
        for kwargs in self._password_map_aliases:
            output["Password Map Aliases"] += "{}{}".format(kwargs["AliasName"], os.linesep)
        print("App Domains")
        for appliance in self.environment.appliances:
            app_domain = self.config["domains"][self.config["appliances"].index(appliance.hostname)]
            print("\t{} -> {}".format(appliance.hostname, app_domain))
        print("Merged DeploymentPolicies")
        for filename in self._merged_deployment_policies:
            print("\t{}".format(filename))
        print("Services")
        for kwargs in self._services:
            print("\t{}: {}".format(kwargs["type"], kwargs["name"]))
        print("-"*80)
        print("Directories to Create")
        for hostname, dirs in list(self.dirs_to_create.items()):
            if dirs:
                print("\t{}".format(hostname))
                for directory in dirs:
                    print("\t\t{}".format(directory))
        print("Uploads")
        for filename, kwargs in list(self._uploads.items()):
            print("\t{} -> {}".format(os.path.relpath(kwargs["file_in"], self.config["repo_dir"]), kwargs["file_out"]))
        print("Imports")
        if self.deployment_policy is not None:
            print("\tmerged_deployment_policy.xcfg")
        for kwargs in self._imports:
            print("\t{}".format(os.path.relpath(kwargs["zip_file"], self.config["repo_dir"])))
        print("Password Map Aliases")
        for kwargs in self._password_map_aliases:
            print("\t{}".format(kwargs["AliasName"]))
        return output

    def execute(self):
        log = make_logger("mast.datapower.deployment.git-deploy")
        output = OrderedDict()
        for index, action in enumerate(self):
            msg = "\nStep {}/{}, {}".format(index, len(self._actions)-1, action.name)
            for k, v in list(action.kwargs.items()):
                if "password" not in k.lower():
                    msg += "\n\t{}={}".format(k, v)
            print(msg)
            log.info("Executing action '{}'".format(repr(action)))
            response = action()
            try:
                response_tree = response.xml
            except AttributeError:
                # Not an XML response, it is a normal backup
                pass

            key = "{}-{}".format(index, action.name)
            # web mode
            if "CreateDir" in action.name:
                output[key] = "Result: {}".format("\n\t".join(response_tree.find(".//{http://www.datapower.com/schemas/management}result").itertext()))
            elif "quiesce" in action.name and "unquiesce" not in action.name:
                output[key] = "Result: {}".format("\n\t".join(response_tree.find(".//{http://www.datapower.com/schemas/management}result").itertext()))
                delay = self.config["quiesce_timeout"] + self.config["quiesce_delay"]
            elif "ObjectStatus" in action.name:
                output[key] = "Down but Enabled Objects"
                readings = response_tree.findall(".//ObjectStatus")
                readings = [node for node in readings if node.findtext("OpState") == "down"]
                readings = [node for node in readings if node.findtext("AdminState") == "enabled"]
                for reading in readings:
                    output[key] += "\n\t{} {} ({})".format(reading.findtext("Class"), reading.findtext("Name"), reading.findtext("ErrorCode"))

            elif "password-map-alias" in action.name:
                output[key] = "Result: {}".format("\n\t".join(response_tree.find(".//{http://www.datapower.com/schemas/management}result").itertext()))
            elif "upload"  in action.name:
                output[key] = "{} -> {}".format(os.path.relpath(action.kwargs["file_in"], self.config["repo_dir"]), action.kwargs["file_out"])
                output[key] += "\nResult: {}".format("\n\t".join(response_tree.find(".//{http://www.datapower.com/schemas/management}result").itertext()))
            elif "import" in action.name:
                output[key] = ""
                output[key] += "Imported Files"
                for file_node in response_tree.findall(".//imported-files/*"):
                    output[key] += "\n\t{} {}".format(file_node.get("name"), file_node.get("status"))
                output[key] += "\nImported Objects"
                for file_node in response_tree.findall(".//imported-objects/*"):
                    output[key] += "\n\t{} {} {}".format(file_node.get("class"), file_node.get("name"), file_node.get("status"))
                output[key] += "\nExec Script Results"
                for file_node in response_tree.findall(".//exec-script-results/*"):
                    output[key] += "\n\t{} {} {}".format(file_node.get("class"), file_node.get("name"), file_node.get("status"))
            elif "unquiesce" in action.name:
                output[key] = "Result: {}".format("\n\t".join(response_tree.find(".//{http://www.datapower.com/schemas/management}result").itertext()))
            elif "remove-oldest-checkpoint" in action.name:
                output[key] = "Result: {}".format("\n\t".join(response_tree.find(".//{http://www.datapower.com/schemas/management}result").itertext()))
            elif "SaveCheckpoint" in action.name:
                output[key] = "Result: {}".format("\n\t".join(response_tree.find(".//{http://www.datapower.com/schemas/management}result").itertext()))
            elif "NormalBackup" in action.name:
                output[key] = "Backup can be downloaded above"
            elif "save-config" in action.name:
                output[key] = "Result: {}".format("\n\t".join(response_tree.find(".//{http://www.datapower.com/schemas/management}result").itertext()))
            if "CreateDir" in action.name:
                print("\n\tResult: {}".format("\n\t\t".join(response_tree.find(".//{http://www.datapower.com/schemas/management}result").itertext())))
                log.info("sleeping for 5 seconds")
                sleep(5)
            elif "quiesce" in action.name and "unquiesce" not in action.name:
                print("\n\tResult: {}".format("\n\t\t".join(response_tree.find(".//{http://www.datapower.com/schemas/management}result").itertext())))
                delay = self.config["quiesce_timeout"] + self.config["quiesce_delay"]
                wait_for_quiesce(action.appliance, action.kwargs["domain"], self._services, self.config["quiesce_timeout"]+15)
            elif "ObjectStatus" in action.name:
                readings = response_tree.findall(".//ObjectStatus")
                readings = [node for node in readings if node.findtext("OpState") == "down"]
                readings = [node for node in readings if node.findtext("AdminState") == "enabled"]
                print("\n\tDown but Enabled Objects")
                for reading in readings:
                    print("\t\t{} {} ({})".format(reading.findtext("Class"), reading.findtext("Name"), reading.findtext("ErrorCode")))

            elif "password-map-alias" in action.name:
                print("\n\tResult: {}".format("\n\t\t".join(response_tree.find(".//{http://www.datapower.com/schemas/management}result").itertext())))
            elif "upload"  in action.name:
                print("\n\tResult: {}".format("\n\t\t".join(response_tree.find(".//{http://www.datapower.com/schemas/management}result").itertext())))
            elif "import" in action.name:
                print("\n\tImported Files")
                for file_node in response_tree.findall(".//imported-files/*"):
                    print("\t\t{} {}".format(file_node.get("name"), file_node.get("status")))
                print("\tImported Objects")
                for file_node in response_tree.findall(".//imported-objects/*"):
                    print("\t\t{} {} {}".format(file_node.get("class"), file_node.get("name"), file_node.get("status")))
                print("\tExec Script Results")
                for file_node in response_tree.findall(".//exec-script-results/*"):
                    print("\t\t{} {} {}".format(file_node.get("class"), file_node.get("name"), file_node.get("status")))
            elif "unquiesce" in action.name:
                print("\n\tResult: {}".format("\n\t\t".join(response_tree.find(".//{http://www.datapower.com/schemas/management}result").itertext())))
                wait_for_unquiesce(action.appliance, action.kwargs["domain"], self._services, self.config["timeout"])
            elif "remove-oldest-checkpoint" in action.name:
                print("\n\tResult: {}".format("\n\t\t".join(response_tree.find(".//{http://www.datapower.com/schemas/management}result").itertext())))
            elif "SaveCheckpoint" in action.name:
                print("\n\tResult: {}".format("\n\t\t".join(response_tree.find(".//{http://www.datapower.com/schemas/management}result").itertext())))
            elif "NormalBackup" in action.name:
                print("\n\tBackup Saved to '{}'".format(action.resp_file))
            elif "save-config" in action.name:
                print("\n\tResult: {}".format("\n\t\t".join(response_tree.find(".//{http://www.datapower.com/schemas/management}result").itertext())))
            if _window is not None:
                _window.put("CLEAR")
        return output

    def _find_services(self):
        ret = []
        for kwargs in self._imports:
            xcfg = etree.parse(kwargs["zip_file"])
            for obj in xcfg.findall(r'.//configuration/*'):
                if obj.tag in DATAPOWER_SERVICE_TYPES:
                    service = {
                        "type": obj.tag,
                        "name": obj.get("name"),
                    }
                    if service not in ret:
                        ret.append(service)
        return ret

    def _plan(self):
        """Build a plan,
        """
        project_root = self.config["repo_dir"]
        env_dir = os.path.join(project_root, self.config["environment"])
        env_config_dir = os.path.join(env_dir, "config")

        ret = []
        self.dirs_to_create = defaultdict(list)
        for appliance in self.environment.appliances:
            app_domain = self.config["domains"][self.config["appliances"].index(appliance.hostname)]

            if app_domain not in appliance.domains:
                raise ValueError("Domain '{}' does not exist on appliance '{}'".format(app_domain, appliance.hostname))
            if not self.config["ignore_save_needed"]:
                # when checking appliances domains (three lines above), a
                # domain status request is issued, we reuse this here to
                # see if a save is needed
                domain_status = etree.fromstring(appliance.last_response)
                save_needed = list(
                    [n for n in domain_status.findall(".//DomainStatus") if n.find("Domain").text == app_domain]
                )[0].find("SaveNeeded").text
                if save_needed == "on":
                    raise ValueError(
                        "domain '{}' on appliance '{}' "
                        "needs to be saved. Use ignore_save_needed option "
                        "to deploy anyway".format(
                            appliance.hostname,
                            app_domain
                        )
                    )

            # Have to get object status first, if we are getting it
            if self.config["object_status"]:
                ret.append(
                    Action(
                        appliance,
                        self.config,
                        "{}-ObjectStatus-Before".format(appliance.hostname),
                        appliance.get_status,
                        domain=app_domain,
                        provider="ObjectStatus",
                    )
                )

            if self.config["quiesce"]:
                services_to_quiesce = []
                for kwargs in self._services:
                    if kwargs in services_to_quiesce:
                        continue
                    services_to_quiesce.append(kwargs)
                    ret.append(
                        Action(
                            appliance,
                            self.config,
                            "{}-quiesce".format(appliance.hostname),
                            appliance.ServiceQuiesce,
                            domain=app_domain,
                            timeout=self.config["quiesce_timeout"],
                            delay=self.config["quiesce_delay"],
                            **kwargs
                        )
                    )
            ret.extend(self.get_predeployment_steps(appliance, app_domain))

            for kwargs in self._password_map_aliases:
                ret.append(
                    Action(
                        appliance,
                        self.config,
                        "{}-password-map-alias".format(appliance.hostname),
                        appliance.AddPasswordMap,
                        domain=app_domain,
                        **kwargs
                    )
                )
            filestore = appliance.get_filestore(app_domain)
            for filename, kwargs in list(self._uploads.items()):
                file_out = kwargs["file_out"]
                if file_out.startswith("local"):
                    target_dir = "/".join(file_out.split("/")[:-1])
                    if not appliance.directory_exists(target_dir, app_domain, filestore) and target_dir not in self.dirs_to_create[appliance.hostname]:
                        if target_dir.rstrip("/") != "local:":
                            self.dirs_to_create[appliance.hostname].append(target_dir)
                            ret.append(
                                Action(
                                    appliance,
                                    self.config,
                                    "{}-CreateDir".format(appliance.hostname),
                                    appliance.CreateDir,
                                    domain=app_domain,
                                    Dir=target_dir,
                                )
                            )
            for filename, kwargs in list(self._uploads.items()):
                file_out = kwargs["file_out"]
                if file_out.startswith("pubcert"):
                    domain = "default"
                elif file_out.startswith("sharedcert"):
                    domain = "default"
                elif file_out.startswith("cert"):
                    domain = app_domain
                elif file_out.startswith("local"):
                    domain = app_domain
                ret.append(
                    Action(
                        appliance,
                        self.config,
                        "{}-upload".format(appliance.hostname),
                        appliance.set_file,
                        domain=domain,
                        **kwargs
                    )
                )
            if self.deployment_policy is not None:
                ret.append(
                    Action(
                        appliance,
                        self.config,
                        "{}-import-deployment-policy".format(appliance.hostname),
                        appliance.do_import,
                        domain=app_domain,
                        zip_file=os.path.join(self.config["out_dir"], "merged_deployment_policy.xcfg"),
                        source_type="XML",
                    )
                )
            for kwargs in self._imports:
                if env_config_dir in kwargs["zip_file"]:
                    # Do not apply deployment policy to env-specific imports
                    ret.append(
                        Action(
                            appliance,
                            self.config,
                            "{}-import".format(appliance.hostname),
                            appliance.do_import,
                            domain=app_domain,
                            **kwargs
                        )
                    )
                else:
                    if self.deployment_policy is not None:
                        ret.append(
                            Action(
                                appliance,
                                self.config,
                                "{}-import".format(appliance.hostname),
                                appliance.do_import,
                                domain=app_domain,
                                deployment_policy=self.deployment_policy,
                                **kwargs
                            )
                        )
                    else:
                        ret.append(
                            Action(
                                appliance,
                                self.config,
                                "{}-import".format(appliance.hostname),
                                appliance.do_import,
                                domain=app_domain,
                                deployment_policy=self.deployment_policy,
                                **kwargs
                            )
                        )

            if self.config["quiesce"]:
                for kwargs in services_to_quiesce:
                    ret.append(
                        Action(
                            appliance,
                            self.config,
                            "{}-unquiesce".format(appliance.hostname),
                            appliance.ServiceUnquiesce,
                            domain=app_domain,
                            **kwargs
                        )
                    )

            # save configuration
            if self.config["save_config"]:
                ret.append(
                    Action(
                        appliance,
                        self.config,
                        "{}-save-config".format(appliance.hostname),
                        appliance.SaveConfig,
                        domain=app_domain
                    )
                )
            # Get object status after we are all done
            if self.config["object_status"]:
                ret.append(
                    Action(
                        appliance,
                        self.config,
                        "{}-ObjectStatus-After".format(appliance.hostname),
                        appliance.get_status,
                        domain=app_domain,
                        provider="ObjectStatus",
                    )
                )
        return ret


    def _find_imports(self):
        project_root = self.config["repo_dir"]
        env_dir = os.path.join(project_root, self.config["environment"])
        env_config_dir = os.path.join(env_dir, "config")
        common_config_dir = os.path.join(project_root, "config")

        ret = []

        services = []

        # Service imports
        if exists(common_config_dir):
            if not self.config["allow_files_in_export"]:
                quit_for_local_uploads(common_config_dir)
            ret.extend(
                [
                    {
                        "zip_file": os.path.join(common_config_dir, filename),
                        "source_type": "XML",
                    }
                    for filename in [x for x in sorted(os.listdir(common_config_dir)) if "EMPTY" not in x.upper()]
                ]
            )

        if exists(env_config_dir):
            if not self.config["allow_files_in_export"]:
                quit_for_local_uploads(env_config_dir)
            ret.extend(
                [
                    {
                        "zip_file": os.path.join(env_config_dir, filename),
                        "source_type": "XML"
                    }
                    for filename in [x for x in sorted(os.listdir(env_config_dir)) if "EMPTY" not in x.upper()]
                ]
            )
        return ret


    def get_predeployment_steps(self, appliance, app_domain):
        """Predeployment now consiste of a checkpoint and normal backup
        in the default domain and the app domain.
        """
        ret = []
        if self.config["checkpoint_app_domain"]:
            if len(appliance.get_existing_checkpoints(app_domain)) == appliance.max_checkpoints(app_domain):
                ret.append(
                    Action(
                        appliance,
                        self.config,
                        "{}-remove-oldest-checkpoint".format(appliance.hostname),
                        appliance.remove_oldest_checkpoint,
                        domain=app_domain,
                    )
                )
            ret.append(
                Action(
                    appliance,
                    self.config,
                    "{}-SaveCheckpoint".format(appliance.hostname),
                    appliance.SaveCheckpoint,
                    domain=app_domain,
                    ChkName="{}_{}".format(
                        app_domain,
                        Timestamp().epoch
                    )
                )
            )
        if self.config["checkpoint_default_domain"]:
            if len(appliance.get_existing_checkpoints("default")) == appliance.max_checkpoints("default"):
                ret.append(
                    Action(
                        appliance,
                        self.config,
                        "{}-remove-oldest-checkpoint".format(appliance.hostname),
                        appliance.remove_oldest_checkpoint,
                        domain="default",
                    )
                )
            ret.append(
                Action(
                    appliance,
                    self.config,
                    "{}-SaveCheckpoint".format(appliance.hostname),
                    appliance.SaveCheckpoint,
                    domain="default",
                    ChkName="{}_{}".format(
                        "default",
                        Timestamp().epoch
                    )
                )
            )

        if self.config["backup_app_domain"]:
            ret.append(
                Action(
                    appliance,
                    self.config,
                    "{}-NormalBackup".format(appliance.hostname),
                    appliance.get_normal_backup,
                    domains=app_domain,
                )
            )
        if self.config["backup_default_domain"]:
            ret.append(
                Action(
                    appliance,
                    self.config,
                    "{}-NormalBackup".format(appliance.hostname),
                    appliance.get_normal_backup,
                    domains="default",
                )
            )
        return ret

    def _find_uploads(self):
        ret = {}
        project_root = self.config["repo_dir"]
        env_dir = os.path.join(project_root, self.config["environment"])

        # upload directories
        env_local_dir = os.path.join(env_dir, "local")
        env_cert_dir = os.path.join(env_dir, "cert")
        env_sharedcert_dir = os.path.join(env_dir, "sharedcert")
        env_pubcert_dir = os.path.join(env_dir, "pubcert")
        common_local_dir = os.path.join(project_root, "local")
        common_cert_dir = os.path.join(project_root, "cert")
        common_sharedcert_dir = os.path.join(project_root, "sharedcert")
        common_pubcert_dir = os.path.join(project_root, "pubcert")



        # gather uploads
        if exists(common_pubcert_dir):
            for filename in [x for x in os.listdir(common_pubcert_dir) if "EMPTY" not in x.upper()]:
                file_out = "pubcert:///{}".format(filename)
                ret[file_out] = {
                        "file_in": os.path.join(common_pubcert_dir, filename),
                        "file_out": file_out,
                }
        if exists(common_cert_dir):
            for filename in [x for x in os.listdir(common_cert_dir) if "EMPTY" not in x.upper()]:
                file_out = "cert:///{}".format(filename)
                ret[file_out] = {
                        "file_in": os.path.join(common_cert_dir, filename),
                        "file_out": file_out,
                }
        if exists(common_sharedcert_dir):
            for filename in [x for x in os.listdir(common_sharedcert_dir) if "EMPTY" not in x.upper()]:
                file_out = "sharedcert:///{}".format(filename)
                ret[file_out] = {
                        "file_in": os.path.join(common_sharedcert_dir, filename),
                        "file_out": file_out,
                }
        if exists(env_pubcert_dir):
            for filename in [x for x in os.listdir(env_pubcert_dir) if "EMPTY" not in x.upper()]:
                file_out = "pubcert:///{}".format(filename)
                ret[file_out] = {
                        "file_in": os.path.join(env_pubcert_dir, filename),
                        "file_out": file_out,
                }
        if exists(env_cert_dir):
            for filename in [x for x in os.listdir(env_cert_dir) if "EMPTY" not in x.upper()]:
                file_out = "cert:///{}".format(filename)
                ret[file_out] = {
                        "file_in": os.path.join(env_cert_dir, filename),
                        "file_out": file_out,
                }
        if exists(env_sharedcert_dir):
            for filename in [x for x in os.listdir(env_sharedcert_dir) if "EMPTY" not in x.upper()]:
                file_out = "sharedcert:///{}".format(filename)
                ret[file_out] = {
                        "file_in": os.path.join(env_sharedcert_dir, filename),
                        "file_out": file_out,
                }
        if exists(common_local_dir):
            for root, dirs, files in os.walk(common_local_dir):
                if files:
                    for filename in [x for x in files if "EMPTY" not in x.upper()]:
                        file_out = "local://{}".format(os.path.join(root, filename))
                        file_out = file_out.replace(common_local_dir, "")
                        file_out = file_out.replace(os.path.sep, "/")
                        ret[file_out] = {
                                "file_in": os.path.join(root, filename),
                                "file_out": file_out,
                        }
        if exists(env_local_dir):
            for root, dirs, files in os.walk(env_local_dir):
                if files:
                    for filename in [x for x in files if "EMPTY" not in x.upper()]:
                        file_out = "local://{}".format(os.path.join(root, filename))
                        file_out = file_out.replace(env_local_dir, "")
                        file_out = file_out.replace(os.path.sep, "/")
                        ret[file_out] = {
                                "file_in": os.path.join(root, filename),
                                "file_out": file_out,
                        }
        return ret

    def _find_password_map_aliases(self):
        ret = []

        project_root = self.config["repo_dir"]
        env_dir = os.path.join(project_root, self.config["environment"])
        # Password alias
        env_password_alias_file = os.path.join(env_dir, "password", "alias-password.map")
        common_password_alias_file = os.path.join(project_root, "password", "alias-password.map")

        # password alias
        if exists(common_password_alias_file):
            with open(common_password_alias_file, "r") as fp:
                for line in fp:
                    try:
                        name, password = xordecode(_b(line)).split(":", 1)
                    except (binascii.Error, ValueError):
                        try:
                            name, password = line.split(":", 1)
                        except ValueError:
                            raise ValueError("Unable to parse Password Map Alias")

                    ret.append(
                        {
                            "AliasName": name.strip(),
                            "Password": password.strip(),
                        }
                    )
        if exists(env_password_alias_file):
            with open(env_password_alias_file, "r") as fp:
                for line in fp:
                    try:
                        name, password = xordecode(_b(line)).split(":", 1)
                    except (binascii.Error, ValueError):
                        try:
                            name, password = line.split(":", 1)
                        except ValueError:
                            raise ValueError("Unable to parse Password Map Alias")
                    ret.append(
                        {
                            "AliasName": name.strip(),
                            "Password": password.strip(),
                        }
                    )
        return ret

    def _merge_deployment_policies(self):
        ret = []
        project_root = self.config["repo_dir"]
        env_dir = os.path.join(project_root, self.config["environment"])
        env_deppol_dir = os.path.join(env_dir, "DeploymentPolicy")
        self._merged_deployment_policies = []
        env_tree = None
        # Get deployment policy
        if exists(env_deppol_dir):
            if len([x for x in os.listdir(env_deppol_dir) if x.endswith(".xcfg")]) > 1:
                raise ValueError("Only one deployment policy permitted In an environmental directory.")
            try:
                deployment_policy_filename = [x for x in os.listdir(env_deppol_dir) if x.endswith(".xcfg")][0]
            except IndexError:
                if not self.config["ignore_no_deployment_policy"]:
                    raise ValueError("Could not find expected DeploymentPolicy directory at '{}'".format(env_deppol_dir))
                else:
                    deployment_policy_filename = None
            if deployment_policy_filename is not None:
                deployment_policy_filename = os.path.join(env_deppol_dir, deployment_policy_filename)
                self._merged_deployment_policies.append(os.path.relpath(deployment_policy_filename, self.config["repo_dir"]))
                env_tree = etree.parse(deployment_policy_filename)
                self.deployment_policy = env_tree.find(".//ConfigDeploymentPolicy").get("name")
        else:
            if not self.config["ignore_no_deployment_policy"]:
                raise ValueError("Could not find expected DeploymentPolicy directory at '{}'".format(env_deppol_dir))
        if self.deployment_policy is not None:
            # Thanks to Joe Morgan for his helpful feature request to make the common deployment
            # policy be defined in the service-config.conf instead of in the repo
            deployment_policy_filename = self.config["global"].get("common_deployment_policy")
            if deployment_policy_filename:
                self._merged_deployment_policies.append(deployment_policy_filename)
                common_tree = etree.parse(deployment_policy_filename)
                deppol = env_tree.find(".//ConfigDeploymentPolicy")
                # AcceptedConfig
                accepted = deppol.findall("AcceptedConfig")
                if accepted:
                    i = list(deppol).index(accepted[-1]) + 1
                else:
                    i = 0
                for node in common_tree.findall(".//ConfigDeploymentPolicy/AcceptedConfig"):
                    deppol.insert(i, node)
                    i += 1
                # FilteredConfig
                filtered = deppol.findall("FilteredConfig")
                if filtered:
                    i = list(deppol).index(filtered[-1]) + 1
                for node in common_tree.findall(".//ConfigDeploymentPolicy/FilteredConfig"):
                    deppol.insert(i, node)
                    i += 1
                # ModifiedConfig
                modified = deppol.findall("ModifiedConfig")
                if modified:
                    i = list(deppol).index(modified[-1]) + 1
                for node in common_tree.findall(".//ConfigDeploymentPolicy/ModifiedConfig"):
                    deppol.insert(i, node)
                    i += 1
        else:
            # self.deployment_policy is None
            deployment_policy_filename = self.config["global"].get("common_deployment_policy")
            if not deployment_policy_filename:
                if not self.config["ignore_no_deployment_policy"]:
                    raise ValueError("Could not find expected any deployment policies")
                else:
                    pass
            else:
                self._merged_deployment_policies.append(deployment_policy_filename)
                env_tree = etree.parse(deployment_policy_filename)
                self.deployment_policy = env_tree.find(".//ConfigDeploymentPolicy").get("name")
        if env_tree is not None:
            with open(os.path.join(self.config["out_dir"], "merged_deployment_policy.xcfg"), "w") as fp:
                fp.write(_s(etree.tostring(env_tree.getroot())))

class Action(object):
    """A class representing a action to be taken as
    part of a deployment.

    This is a fairly simple wrapper around
    ``functools.partial()``. It is a callable
    action which executes a function, but there
    is some more features which are designed to
    allow easy construction of plans
    """
    def __init__(self, appliance, config, *args, **kwargs):
        self.appliance  = appliance
        self.config = config
        self.name = args[0]
        self.args = args
        self.kwargs = kwargs
        self.callable = partial(args[1], *args[2:], **kwargs)

    def __call__(self):
        log = make_logger("mast.datapower.deployment.git-deploy")
        ret = self.callable()
        req_file = os.path.join(
            self.config["out_dir"],
            "{}-{}-{}.xml".format(
                self.config["step_number"],
                "request",
                self.name,
            )
        )
        resp_file = os.path.join(
            self.config["out_dir"],
            "{}-{}-{}.xml".format(
                self.config["step_number"],
                "response",
                self.name,
            )
        )
        if "NormalBackup" in self.name:
            resp_file = resp_file.replace(".xml", ".zip")
        self.req_file = req_file
        self.resp_file = resp_file
        with open(req_file, "wb") as fp:
            fp.write(_b(str(self.appliance.request)))
        with open(resp_file, "wb") as fp:
            if "NormalBackup" in self.name:
                fp.write(ret)
            else:
                try:
                    fp.write(self.appliance.last_response)
                except TypeError:
                    fp.write(str(self.appliance.last_response))
        if "NormalBackup" in self.name:
            if ZipFile(resp_file).testzip() is not None:
                raise RuntimeError("Found corrupt backup for {} {}".format(
                    self.appliance.hostname, self.kwargs["domain"]
                ))
        self.config["step_number"] += 1
        return ret

    def __str__(self):
        return "\n".join(
            [
                "{}={}".format(k, repr(v)) for k, v in list(self.kwargs.items()) if "password" not in k.lower()
            ]
        )

    def __repr__(self):
        return "{}({})".format(
            self.name,
            ", ".join(
                [
                    "{}={}".format(k, repr(v)) for k, v in list(self.kwargs.items()) if "password" not in k.lower()
                ]
            )
        )

def parse_config(appliances, credentials, environment, service, check_hostname, timeout):
    config_filename = os.path.join(os.environ.get("MAST_HOME"), "etc", "local", "service-config.conf")
    if not os.path.exists(config_filename):
        raise ValueError("Did not find configuration at '{}'".format(config_filename))
    with open(config_filename, "r") as fp:
        headers = list(
            [line for line in fp if line.startswith("[")]
        )
    if len(headers) != len(set(headers)):
        duplicates = [k for k,v in list(Counter(headers).items()) if v>1]
        raise ValueError("Duplicate header(s) {} found in '{}'".format(duplicates, config_filename))
    config = get_config("service-config.conf")
    service_config = config.items(service)
    for key, value in service_config:
        if "-" in key:
            appl, env = key.split("-")
            if datapower.environment.is_environment(appl):
                config.remove_option(service, key)
                for hostname in datapower.environment.get_environment(appl).hostnames:
                    if not config.has_option(service, "{}-{}".format(hostname, env)):
                        config.set(service, "{}-{}".format(hostname, env), value)
    ret = {
        "appliances": [],
        "credentials": [],
        "domains": [],
        "environment": environment,
        "global": dict(config.items("global")),
    }
    _environment = datapower.Environment(
        appliances,
        credentials=credentials,
        check_hostname=check_hostname,
        timeout=timeout,
    )
    ret["_environment"] = _environment
    for index, appliance in enumerate(_environment.appliances):
        domain = config.get(
            service,
            "{}-{}".format(appliance.hostname, environment),
            fallback=None,
        )
        if domain is None:
            raise ValueError("Appliance '{}' not part of environment '{}'".format(
                appliance.hostname, environment
            ))
        ret["appliances"].append(appliance.hostname)
        if len(credentials) == 1:
            ret["credentials"].append(credentials[0])
        else:
            try:
                ret["credentials"].append(credentials[index])
            except IndexError:
                raise ValueError("Must provide either one set of credentials or one set for each appliance")
        ret["domains"].append(domain)
    ret.update(config.items(service))
    ret["repo"] = ret["repo"].rstrip("/\\")
    if "git-credentials" in ret:
        username, password = xordecode(ret["git-credentials"]).split(":", 1)
        url = urlparse(ret["repo"])
        ret["repo"] = "{}://{}:{}@{}{}".format(
            url.scheme,
            quote_plus(username),
            quote_plus(password),
            url.netloc,
            url.path,
        )
    return ret


def _prepare_output_directories(config, out_dir):
    """Ensure that output directories exist, add absolute paths to config
    """
    log = make_logger("mast.datapower.deployment.git-deploy")
    # Build the paths for out, audit and repo
    if not out_dir:
        out_dir = os.path.join(os.environ["MAST_HOME"], "tmp", "deployment-results")
    config["out_dir"] = out_dir
    config["audit_dir"] = os.path.join(out_dir, "audit")
    config["repo_dir"] = os.path.join(
        config["global"]["clone_dirs"],
        ntpath.normpath(ntpath.basename(config["repo"])),
    )
    config["repo_dir"] = config["repo_dir"]

    # Create the out and audit directories if needed
    if not os.path.exists(config["out_dir"]):
        log.info("'{}' does not exist, creating...".format(config["out_dir"]))
        os.makedirs(config["out_dir"])
    if not os.path.exists(config["audit_dir"]):
        log.info("'{}' does not exist, creating...".format(config["audit_dir"]))
        os.makedirs(config["audit_dir"])
    if not os.path.exists(config["global"]["clone_dirs"]):
        log.info("'{}' does not exist, creating...".format(config["global"]["clone_dirs"]))
        os.makedirs(config["global"]["clone_dirs"])


def _initialize_logging(config):
    """Add a custom logging handler so we can include the logs in
    the post-deployment artifacts
    """
    log = make_logger("mast.datapower.deployment.git-deploy")
    handler = logging.FileHandler(
        os.path.join(
            config["out_dir"],
            "deployment.log"
        ),
        "w"
    )
    formatter = logging.Formatter("%(asctime)s: %(levelname)s: %(relativeCreated)d: %(message)s")
    handler.setFormatter(formatter)
    log.addHandler(handler)

    log_2 = make_logger("mast.datapower.deployment.results")
    handler_2 = logging.FileHandler(
        os.path.join(
            config["out_dir"],
            "results.log"
        ),
        "w"
    )
    formatter_2 = logging.Formatter("%(message)s")
    handler_2.setFormatter(formatter_2)
    log_2.addHandler(handler_2)


def _clone_pull_and_checkout(config):
    """Either pull latest changes or clone the remote repository
    """
    log = make_logger("mast.datapower.deployment.git-deploy")
    git = config["global"]["git_executable"]
    if exists(config["repo_dir"]):
        log.info("Existing local repository found, pulling latest changes")
        with working_directory(config["repo_dir"]):
            out, err = system_call("{} pull".format(git))
    else:
        _repo = config["repo"]
        if "git-credentials" in config:
            _remove_this = ":".join(
                map(
                    quote_plus,
                    xordecode(
                        config["git-credentials"]
                    ).split(":", 1)
                )
            ) + "@"
            _repo = config["repo"].replace(_remove_this, "")
        log.info("cloning repo '{}' to '{}'".format(_repo, config["repo_dir"]))
        out, err = system_call("{} clone {} {}".format(git, config["repo"], config["repo_dir"]))
    print(out)
    print(err)
    log.info("stdout from git: '{}'".format(out))
    log.info("stderr from git: '{}'".format(err))
    # If commit is provided, perform a git checkout
    if config["commit"]:
        with working_directory(config["repo_dir"]):
            log.info("performing 'git checkout {}'".format(config["commit"]))
            out, err = system_call("{} checkout {}".format(git, config["commit"]))
        print(out)
        print(err)
        log.info("stdout from git checkout: '{}'".format(out))
        log.info("stderr from git checkout: '{}'".format(err))
    # Copy the repo at the state of deployment to out_dir for auditing purposes
    dst = os.path.join(
        config["out_dir"],
        ntpath.normpath(ntpath.basename(config["repo"])),
    )
    if os.path.exists(dst):
        shutil.rmtree(dst, onerror=handleRemoveReadonly)
    shutil.copytree(config["repo_dir"], dst)

class GitDeploy(ApplianceOptions):
    # Not ApplianceParams: the environment here is the name of an
    # environment in service-config.conf.
    environment: str = Option(
        "", short="e",
        help="The environment must be defined in the "
             "service-config.conf")
    service: str = Option(
        "", short="s",
        help="The service to deploy, this corresponds to a stanza in "
             "service-config.conf")
    commit: str = Option(
        "", short="C",
        help="The commit id, commit tag or branch for which to perform "
             "a git checkout")
    out_dir: str = Option(
        "", short="o", widget=OUT_DIR,
        help="The directory in which to store the deployment artifacts "
             "(default: `$MAST_HOME/tmp/deployment-results`)")
    dry_run: bool = Option(
        False, short="d",
        help="If specified, nothing will be done to the appliances")
    allow_files_in_export: bool = Option(
        False, short="A",
        help="If specified, deployment will proceed regardless of "
             "whether there are local uploads within configuration "
             "exports")
    ignore_save_needed: bool = Option(
        False, short="i",
        help="If specified, deployment will proceed regardless of "
             "whether the app domain needs to be saved")
    ignore_no_deployment_policy: bool = Option(
        False, short="I",
        help="If specified, deployment will proceed even if there are "
             "no deployment policies")
    quiesce: bool = Option(
        True,
        help="If specified, the service will not be quiesced before "
             "the deployment")
    object_status: bool = Option(
        True, help="If specified, no object status will be taken")
    backup_app_domain: bool = Option(
        True, help="If specified, the app domain will not be backed up")
    backup_default_domain: bool = Option(
        True,
        help="If specified, the default domain will not be backed up")
    checkpoint_app_domain: bool = Option(
        True,
        help="If specified, no checkpoint will be set in the app domain")
    checkpoint_default_domain: bool = Option(
        True,
        help="If specified, no checkpoint will be set in the default "
             "domain")
    quiesce_delay: int = Option(
        0, short="q",
        help="The number of seconds the datapower will wait before "
             "quiescing the service")
    quiesce_timeout: int = Option(
        60, short="Q",
        help="The maximum number of seconds for the datapower to wait "
             "for the service to quiesce before abruptly terminating "
             "it.")
    save_config: bool = Option(
        False, short="S",
        help="If specified, the app domains configuration will be "
             "saved after the deployment is complete")
    progress_window: bool = Option(
        False,
        help="If specified, a window on the machine that runs MAST "
             "shows the progress of the deployment. It needs tkinter "
             "and a display.")


def git_deploy(params: GitDeploy):
    """Deploy services to IBM DataPower appliances. A service must be
configured in $MAST_HOME/etc/local/service-config.conf, please
see $MAST_HOME/etc/default/service-config.conf for documentation
on the format of this configuration file."""
    global _window
    log = make_logger("mast.datapower.deployment.git-deploy")

    config = parse_config(
        params.appliances,
        params.credentials,
        params.environment,
        params.service,
        not params.no_check_hostname,
        params.timeout,
    )
    config.update(
        {
            "allow_files_in_export": params.allow_files_in_export,
            "ignore_save_needed": params.ignore_save_needed,
            "ignore_no_deployment_policy": params.ignore_no_deployment_policy,
            "quiesce": params.quiesce,
            "quiesce_delay": params.quiesce_delay,
            "quiesce_timeout": params.quiesce_timeout,
            "save_config": params.save_config,
            "step_number": 0,
            "object_status": params.object_status,
            "backup_app_domain": params.backup_app_domain,
            "backup_default_domain": params.backup_default_domain,
            "checkpoint_app_domain": params.checkpoint_app_domain,
            "checkpoint_default_domain": params.checkpoint_default_domain,
            "commit": params.commit,
            "service": params.service,
            "timeout": params.timeout,
        }
    )
    # This adds paths to config and creates the directories as needed
    _prepare_output_directories(config, params.out_dir)

    _initialize_logging(config)

    log.info("Working with path: '{}'".format(os.environ.get("PATH")))

    display = None
    if params.progress_window:
        _window = StdoutQueue()
        display = StdoutDisplay(_window)
        display.start()
        sleep(2)

    try:
        _clone_pull_and_checkout(config)

        plan = Plan(config)

        if params.dry_run:
            output = plan.list_steps()
        else:
            output = plan.execute()
    finally:
        if display is not None:
            sleep(5)
            _window.put("END")
            _window = None

    # The steps were reported as they happened
    report = Report(plan.environment)
    report.add(StatusResults(output), show=False)
    return report


if __name__ == "__main__":
    cli = Cli()
    cli.command("git-deploy")(git_deploy)
    cli.run()
