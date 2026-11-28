[Back to index](./index.html)
<h1>MAST for IBM DataPower Version {0}</h1>
<h2>Getting Started Guide</h2>

[TOC]

# Introduction

Welcome to the MAST for IBM DataPower Getting Started Guide. In this guide
we will cover some basic configuration and use of MAST for IBM DataPower.
Please note, however, that upon installation MAST for IBM DataPower is ready
for use, but to make the product easier to use and to maximize your investment
in the product some simple configuration will help.


NOTE: Throughout this document we will refer to $MAST_HOME, please be advised
that this will refer to the directory into which MAST was installed

# First thing first

While there are a few different ways in which MAST can be secured, the most
obvious one and the most necessary one is to use https for the mast web ui.

The web GUI uses TLS by default, and a fresh install has no certificate.
Until it has one, the web GUI does not start, and it says what to do.
Either set `key` and `cert` in the `[server]` section of
`$MAST_HOME/etc/local/server.conf` to the files of your private key and
certificate, or make a self-signed certificate with the script that comes
with MAST. Run it in `$MAST_HOME`:

    :::bash
    ./mast-python contrib/gen-cert.py

On Windows, run `mast-python.bat contrib\gen-cert.py`. The script puts the
certificate in `etc/crypto/cert/mast.crt` and the private key in
`etc/crypto/private/mast.key`, where the default `server.conf` expects
them. Only the owner can read the key. The certificate names this
computer, `localhost`, `127.0.0.1` and `::1`; add other names with
`--altName`, once for each name. It does not replace a key that is there
unless you give `--force`. `./mast-python contrib/gen-cert.py -h` lists the
other options, such as the fields of the subject.

Browsers warn about a self-signed certificate until you trust it. For a
server that other people use, a certificate from your certificate
authority is better.


Every page of the web GUI needs a login, and so does the command line.
Add the first admin in `$MAST_HOME` (on Windows, `mast.bat users`); it
asks for a password. Then log in on the command line, and give yourself
the commands of DataPower:

    :::bash
    ./mast users add-first-admin -u NAME
    ./mast login -u NAME
    ./mast groups add-member -n datapower-operator -u NAME

Then open the web GUI and log in. The Operator's Guide explains the
permission sets and groups, service accounts for cron jobs and
scripts, and the other `mast users` commands, and has a short tour of the
web UI. The Installation Guide lists the steps of a first run, from the
certificate to a stored credential.

# Your appliances and fleets

MAST keeps the targets that it knows in its database: the registry. A
DataPower appliance is a target of the kind `datapower`. Give each one
a name that is easy to type, and the address to connect to:

    :::bash
    ./mast targets add -n dev_dp_1 -k datapower -A 10.0.1.1
    ./mast targets add -n dev_dp_2 -k datapower -A 10.0.1.2
    ./mast targets add -n prod_dp_1 -k datapower -A 10.2.1.1
    ./mast targets add -n prod_dp_2 -k datapower -A 10.2.1.2 -s soma_port=5551

The settings of an appliance are the attributes of its kind
(`./mast plugins kinds` lists them), given as `-s NAME=VALUE`. A port
that is not set is the standard port of DataPower (SOMA 5550, WebGUI
9090, SSH 22). Another setting that is not set (the SOMA spec file and
so on) takes the default of `[global]` in
`$MAST_HOME/etc/default/appliances.conf`, which you can change in
`$MAST_HOME/etc/local/appliances.conf`.

A fleet is a named group of targets of one kind. A command that is
given the name of a fleet acts on all of its targets:

    :::bash
    ./mast fleets add -n dev -k datapower -m dev_dp_1 -m dev_dp_2
    ./mast fleets add -n prod -k datapower -p -m prod_dp_1 -m prod_dp_2
    ./mast fleets add -n all -k datapower -m dev_dp_1 -m dev_dp_2 -m prod_dp_1 -m prod_dp_2

`-p` marks a production fleet. `./mast targets list` and
`./mast fleets list` show what is there; `update`, `remove`,
`set-members` and `fleets remove` change it. An admin can do the same
in the web UI, under Admin → Targets.

Commands run only on registered targets: add an appliance before you
give its name to a command.

## Coming from MAST 3.15.2 or older

Older versions of MAST kept the aliases in `etc/local/hosts.conf`, the
environments in `etc/local/environments.conf`, and the settings of
single appliances in sections of `etc/local/appliances.conf`. MAST no
longer reads the first two, and warns while they are there. Import them
once:

    :::bash
    ./mast targets import --dry-run     # what would be done
    ./mast targets import

Every alias, every name in an environment, and every section of
`appliances.conf` for one appliance becomes an appliance. The files are
renamed to `*.imported`; `appliances.conf` keeps its `[global]`
section. To go back to an older MAST, `./mast targets export` writes
the three files again.

# Configuring the Server

Since the MAST Web GUI is a hosted web app, it is possible to configure
certain options related to the server. The available options are well
documented within the file `$MAST_HOME/etc/default/server.conf` but some
of the most important ones are under the [server] stanza. Here you can set
the listening host and port as well as the private key and public certificate
also in this stanza you can specify secure which is corresponds to whether or
not to use TLS. Please see the documentation in server.conf.

# Configuring Logging

There are a few options you can use to configure the logging of MAST. Please
see the documentation in logging.conf on what the various options mean.

# Basic Usage

NOTE: We will be using the appliances and environments we defined above
in order to make the examples more concise.

NOTE: Although we will not include it in the commands, if you have not
replaced the key pair for the xml management interface, or the key-pair
is not in your system's trust-store, you will need to add a "-n" to each
of these commands to disable hostname verification.

We will cover some basic usage of MAST here, but for more details see the
[CLI Reference](./CLIReference.html) and the [API Reference](./APIReference.html). We
will go over some common use-case scenarios and complete them using the CLI.
Here are the scenarios we will cover:

* Add a user to all appliances, and force them to change their password on login
* Add a domain to a group of appliances
* Import a configuration ZIP file into a group of appliances

## Adding a User

Our use case is this:

A new developer has joined our team, and we need to add a user to the dev
DataPower appliances and associate this user with the developer group

In order to list the users we currently have on the appliances, we can
issue the following command:

    :::bash
    $ ./mast datapower list-users -a dev -c username:password
    dev_dp_1
    ========
        user_1
        user_2
        user_3

    dev_dp_2
    ========
        user_1
        user_2
        user_3
        user_4

    Common
    ======
        user_1
        user_2
        user_3

We can see here that there is one user which only exists on the dev_dp_2
appliance. This may or may not be purposeful (and I've just included it here
to demonstrate how we would see something like that). For our task, however,
this is irrelevant. We need to add the new user to the dev appliances. We can
do that with the following command:

    :::bash
    $ ./mast datapower add-user -a dev -c username:password -u new_user -p pa55word -g developer -s

This will add a user to both dev appliances and associate them with the
developer group. We can then verify that it was added by listing the users
just as we did above:

    :::bash
    $ ./mast datapower list-users -a dev -c username:password
    dev_dp_1
    ========
        user_1
        user_2
        user_3
        new_user

    dev_dp_2
    ========
        user_1
        user_2
        user_3
        user_4
        new_user

    Common
    ======
        user_1
        user_2
        user_3
        new_user

The "-s" flag to the add-users command told MAST to save the configuration
after the user was added, so we could stop here, but we should probably
ensure that our new user needs to change their password on the next login. We
can do that with the following command:

    :::bash
    $ ./mast datapower force-change-password -a dev -c username:password -U new_user

Now when the new user goes to log onto the appliance, they will be forced to
change their password. That concludes our first use case, now we will look at
adding a domain to a group of appliances.

## Adding a Domain

Our use case is this:

We have a new application which is starting out in the dev environment. We
would like a new domain where our developers can work on the configurations
and services which will make up this application.

So probably the first thing we should do is look at which domains exist on
our development appliances. We can do that with the following command:

    :::bash
    $ ./mast datapower list-domains -a dev -c username:password
    dev_dp_1
    ========
        default
        domain_1
        domain_2

    dev_dp_2
    ========
        default
        domain_1
        domain_2
        domain_3

    Common
    ======
        default
        domain_1
        domain_2

We can see again that dev_dp_2 has one domain that doesn't exists on dev_dp_1,
but that's not our concern right now. We need to add a domain named new_domain
to both boxes. We can do that with the following command:

    :::bash
    $ ./mast datapower add-domain -a dev -c username:password -d new_domain -s

This will add our domain and the "-s" flag will cause MAST to save the
configuration. Now all that is left to do is to list all the domains again,
just to be sure that it was added.

    :::bash
    $ ./mast datapower list-domains -a dev -c username:password
    dev_dp_1
    ========
        default
        domain_1
        domain_2
        new_domain

    dev_dp_2
    ========
        default
        domain_1
        domain_2
        domain_3
        new_domain

    Common
    ======
        default
        domain_1
        domain_2
        new_domain

As we can see our new domain is now on both boxes. Next we will look at how to
import a configuration ZIP file into our new domain.

## Importing Configuration

So, we have our new developer and we created a new application domain for her
new project. Now our new developer is so eager to begin that she has already
created her first new service in preparation of her new application. It is our
task to import the configuration into our dev environment. We can do that with
this command:

    :::bash
    $ ./mast datapower import -a dev -c username:password -D new_domain -f ~/Downloads/export.zip

Now, there is a reason that this command does not persist the changes and that
is to give the interested parties a chance to "check-out" the results. Once
that's done, you can save the configuration with the following command:

    :::bash
    $ ./mast datapower save -a dev -c username:password -D new_domain

That's it, simple really, but this is all in dev. There are a lot more
restrictions about making changes in our qa and prod environments.

# Jobs

Each command that we ran above was recorded as a job, with its progress,
its result and its status. You can list the newest jobs, and look at one
of them, with mast jobs. The start of the id of a job is enough:

    :::bash
    $ ./mast jobs list
    $ ./mast jobs show -j 3f2a

A command exits with status 1 if it failed on any of the appliances, so a
script can check `$?`. Ctrl-C cancels the job of
a command at its next step.

With a worker running (`./mast worker`, or the one that mastd hosts), a
long command can be left to the worker. `--detach` prints the id of the
job and returns at once:

    :::bash
    $ ./mast datapower normal-backup -a dev -c username:password -D all-domains --detach
    $ ./mast jobs follow -j 3f2a

The [Installation Guide](./installation.html) explains how to set up the
job queue, including how to use a PostgreSQL database.

# Conclusion

That concludes our getting started guide. Please contact [support@mcindi.com](mailto://support@mcindi.com)
if you have additional questions about getting started.
