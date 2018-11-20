import contextlib
import os
import sys

import tox
from tox import hookimpl


def _init_pipenv_environ():
    os.environ["PIPENV_ACTIVE"] = "1"

    # Ignore host virtual env
    os.environ["PIPENV_IGNORE_VIRTUALENVS"] = "1"

    # Answer yes on recreation of virtual env
    os.environ["PIPENV_YES"] = "1"

    # don't use pew
    # os.environ["PIPENV_VENV_IN_PROJECT"] = "1"

    # The above environment flag creates a venv in the
    # <your_project>/.tox/<envname>/.venv directory
    # This however does not repect the site-packages flag
    # leading to ALL the packages being installed again for each environment
    # separately, even if they are already installed in the parent environment.
    # This can certainly be advantagous for some projects,
    # but for projects with many dependencies this adds unecessary overhead for
    # unit-tests.
    #
    # Not setting the environement variable, ie. using pew, creates the virtual
    # environment under ~/.local/share/virtualenvs/<tox_env>/
    # This environment will only contain the dependencies needed for running
    # tests of a specific test environment, significanly reducing setup time.
    #
    # Whether the undesired effect is due pipenv or tox-pipenv remains unclear.
    # Not setting the environment variable is not the perfect solution, but
    # solves my immediate issues.


def _clone_pipfile(venv):
    root_pipfile_path = venv.session.config.toxinidir.join("Pipfile")
    # venv path may not have been created yet
    venv.path.ensure(dir=1)

    venv_pipfile_path = venv.path.join("Pipfile")
    if not root_pipfile_path.exists():
        with open(str(root_pipfile_path), "a"):
            os.utime(str(root_pipfile_path), None)

    if not venv_pipfile_path.check():
        root_pipfile_path.copy(venv_pipfile_path)
    return venv_pipfile_path


@contextlib.contextmanager
def wrap_pipenv_environment(venv, pipfile_path):
    old_pipfile = os.environ.get("PIPENV_PIPFILE", None)
    old_pipvenv = os.environ.get("PIPENV_VIRTUALENV", None)
    old_venv = os.environ.get("VIRTUAL_ENV", None)
    os.environ["PIPENV_PIPFILE"] = str(pipfile_path)
    os.environ["PIPENV_VIRTUALENV"] = os.path.join(str(venv.path))
    os.environ["VIRTUAL_ENV"] = os.path.join(str(venv.path))
    yield
    if old_pipfile:
        os.environ["PIPENV_PIPFILE"] = old_pipfile
    if old_pipvenv:
        os.environ["PIPENV_VIRTUALENV"] = old_pipvenv
    if old_venv:
        os.environ["VIRTUAL_ENV"] = old_venv


@hookimpl
def tox_testenv_create(venv, action):
    _init_pipenv_environ()

    config_interpreter = venv.getsupportedinterpreter()
    args = [sys.executable, "-m", "pipenv"]
    if venv.envconfig.sitepackages:
        args.append("--site-packages")

    args.extend(["--python", str(config_interpreter)])

    venv.session.make_emptydir(venv.path)
    basepath = venv.path.dirpath()
    basepath.ensure(dir=1)
    pipfile_path = _clone_pipfile(venv)

    with wrap_pipenv_environment(venv, pipfile_path):
        venv._pcall(args, venv=False, action=action, cwd=basepath)

    # Return non-None to indicate the plugin has completed
    return True


@hookimpl
def tox_testenv_install_deps(venv, action):
    _init_pipenv_environ()
    # TODO: If skip_install set, check existence of venv Pipfile
    deps = venv._getresolvedeps()
    basepath = venv.path.dirpath()
    basepath.ensure(dir=1)
    pipfile_path = _clone_pipfile(venv)
    args = [sys.executable, "-m", "pipenv", "install", "--dev"]
    if action.venv.envconfig.pip_pre:
        args.append('--pre')
    with wrap_pipenv_environment(venv, pipfile_path):
        if deps:
            action.setactivity("installdeps", ",".join([str(x) for x in deps]))
            args += [str(x) for x in deps]
        else:
            action.setactivity("installdeps", "[]")
        venv._pcall(args, venv=False, action=action, cwd=basepath)

    # Return non-None to indicate the plugin has completed
    return True


@hookimpl
def tox_runtest(venv, redirect):
    _init_pipenv_environ()
    pipfile_path = _clone_pipfile(venv)

    action = venv.session.newaction(venv, "runtests")

    with wrap_pipenv_environment(venv, pipfile_path):
        action.setactivity(
            "runtests", "PYTHONHASHSEED=%r" % os.environ.get("PYTHONHASHSEED")
        )
        for i, argv in enumerate(venv.envconfig.commands):
            # have to make strings as _pcall changes argv[0] to a local()
            # happens if the same environment is invoked twice
            cwd = venv.envconfig.changedir
            msg = "commands[%s] | %s" % (i, " ".join([str(x) for x in argv]))
            action.setactivity("runtests", msg)
            # check to see if we need to ignore the return code
            # if so, we need to alter the command line arguments
            if argv[0].startswith("-"):
                ignore_ret = True
                if argv[0] == "-":
                    del argv[0]
                else:
                    argv[0] = argv[0].lstrip("-")
            else:
                ignore_ret = False
            args = [sys.executable, "-m", "pipenv", "run"] + argv
            try:
                venv._pcall(
                    args,
                    venv=False,
                    cwd=cwd,
                    action=action,
                    redirect=redirect,
                    ignore_ret=ignore_ret
                )
            except tox.exception.InvocationError as err:
                if venv.envconfig.ignore_outcome:
                    venv.session.report.warning(
                        "command failed but result from testenv is ignored\n"
                        "  cmd: %s" % (str(err),)
                    )
                    venv.status = "ignored failed command"
                    continue  # keep processing commands

                venv.session.report.error(str(err))
                venv.status = "commands failed"
                if not venv.envconfig.ignore_errors:
                    break  # Don't process remaining commands
            except KeyboardInterrupt:
                venv.status = "keyboardinterrupt"
                venv.session.report.error(venv.status)
                raise

    return True


@hookimpl
def tox_runenvreport(venv, action):
    _init_pipenv_environ()
    pipfile_path = _clone_pipfile(venv)

    basepath = venv.path.dirpath()
    basepath.ensure(dir=1)
    with wrap_pipenv_environment(venv, pipfile_path):
        action.setactivity("runenvreport", "")
        # call pipenv graph
        args = [sys.executable, "-m", "pipenv", "graph"]
        output = venv._pcall(args, venv=False, action=action, cwd=basepath)

        output = output.split("\n")
    return output
