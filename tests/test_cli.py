import io
import os
import shutil
import unittest
from unittest import mock

import aioredis
import arrow
import cfg4py
import omicron
import pytest
from omicron import tf
from pyemit import emit
from ruamel.yaml import YAML

from omega import cli
from omega.config import get_config_dir
from omega.worker.abstract_quotes_fetcher import AbstractQuotesFetcher as aq
from tests import init_test_env


@pytest.mark.skip
class TestCLI(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.cfg = await init_test_env()

        # setup need these info
        os.environ["__cfg4py_server_role__"] = "DEV"
        os.environ["TZ"] = "Asia/Shanghai"
        os.environ["REDIS_HOST"] = "127.0.0.1"
        os.environ["REDIS_PORT"] = "6379"
        os.environ["POSTGRES_USER"] = "zillionare"
        os.environ["POSTGRES_PASSWORD"] = "123456"
        os.environ["POSTGRES_HOST"] = "127.0.0.1"
        os.environ["POSTGRES_PORT"] = "5432"
        os.environ["POSTGRES_DB"] = "zillionare"
        os.environ["POSTGRES_ENABLED"] = "True"

        # disable catch up sync, since test_cli, jobs will be launched many times and cache might by empty
        redis = await aioredis.create_redis(self.cfg.redis.dsn)

        last_sync = arrow.now(self.cfg.tz).format("YYYY-MM-DD HH:mm:SS")
        await redis.set("jobs.bars_sync.stop", last_sync)

        redis.close()
        await redis.wait_closed()

        await self.create_quotes_fetcher()
        # await tf.service_degrade()

    async def create_quotes_fetcher(self):
        cfg = cfg4py.get_instance()
        fetcher_info = cfg.quotes_fetchers[0]
        impl = fetcher_info["impl"]
        account = fetcher_info["account"]
        password = fetcher_info["password"]
        await aq.create_instance(impl, account=account, password=password)

    async def asyncTearDown(self) -> None:
        await emit.stop()

    def yaml_dumps(self, settings):
        stream = io.StringIO()
        yaml = YAML()
        yaml.indent(sequence=4, offset=2)
        try:
            yaml.dump(settings, stream)
            return stream.getvalue()
        finally:
            stream.close()

    async def test_omega_lifecycle(self):
        await cli.start("worker")
        procs = cli.find_fetcher_processes()
        self.assertTrue(len(procs) >= 1)

        await cli.restart("worker")
        await cli.status()

        await cli.stop("worker")
        procs = cli.find_fetcher_processes()
        self.assertEqual(0, len(procs))

        await cli.start()
        await cli.restart()
        await cli.stop()

    async def test_omega_jobs(self):
        await cli.start("jobs")
        await cli.status()
        await cli.stop("jobs")

    def test_load_factory_settings(self):
        settings = cli.load_factory_settings()
        self.assertTrue(len(settings) > 0)
        self.assertEqual("Asia/Shanghai", settings.get("tz"))

    def test_update_config(self):
        settings = cli.load_factory_settings()

        key = "postgres.dsn"
        value = "postgres://blah"
        cli.update_config(settings, key, value)
        self.assertEqual(settings["postgres"]["dsn"], value)

        key = "tz"
        value = "shanghai"
        cli.update_config(settings, key, value)
        self.assertEqual(settings[key], value)

    def test_check_environment(self):
        os.environ[cfg4py.envar] = ""

        with mock.patch("builtins.input", side_effect=["C"]):
            os.environ[cfg4py.envar] = "PRODUCTION"
            self.assertTrue(cli.check_environment())

    def test_config_logging(self):
        settings = {}
        folder = "/tmp/omega/test"
        shutil.rmtree("/tmp/omega/test", ignore_errors=True)

        # 1. function normal
        with mock.patch("builtins.input", return_value=folder):
            cli.config_logging(settings)

        try:
            logfile = os.path.join(folder, "omega.log")
            with open(logfile, "rt") as f:
                content = f.read(-1)
                msg = f"logging output is writtern to {logfile} now"
                self.assertTrue(content.find(msg))
        except Exception as e:
            print(e)

        # # fixme: disable temporarily, patch('sh.contrib.sudo.mkdir') causes issue
        # # 2. no permission to mkdir.
        # with mock.patch("os.makedirs", side_effect=PermissionError()):
        #     with mock.patch("sh.contrib.sudo.mkdir"):
        #         with mock.patch("sh.contrib.sudo.chmod"):
        #             pass  # disable prompt to enable auto test

        # 3. raise other exception, need redo
        shutil.rmtree("/tmp/omega/test", ignore_errors=True)
        with mock.patch("os.makedirs", side_effect=[Exception("mocked"), mock.DEFAULT]):
            with mock.patch("builtins.input", return_value=folder):
                with mock.patch("omega.cli.choose_action", return_value="R"):
                    try:
                        cli.config_logging(settings)
                    except FileNotFoundError:
                        # since os.makdirs are mocked, so there's no logfile created
                        pass

            self.assertEqual(2, os.makedirs.call_count)

    async def test_config_postgres(self):
        settings = {}
        with mock.patch(
            "builtins.input",
            side_effect=["R", "127.0.0.1", "6380", "account", "password", "zillionare"],
        ):
            with mock.patch("omega.cli.check_postgres", side_effect=[True]):
                await cli.config_postgres(settings)
                expected = "postgres://account:password@127.0.0.1:6380/zillionare"
                self.assertEqual(expected, settings["postgres"]["dsn"])

        # test continue with wrong config
        with mock.patch(
            "builtins.input",
            side_effect=[
                "R",
                "127.0.0.1",
                "6380",
                "account",
                "password",
                "zillionare",
                "C",
            ],
        ):
            await cli.config_postgres(settings)
            expected = "postgres://account:password@127.0.0.1:6380/zillionare"
            self.assertEqual(expected, settings["postgres"]["dsn"])

        # check connection to postgres. Need provide right info in ut
        host = os.environ.get("POSTGRES_HOST")
        port = os.environ.get("POSTGRES_PORT")
        db = os.environ.get("POSTGRES_DB")
        user = os.environ.get("POSTGRES_USER")
        password = os.environ.get("POSTGRES_PASSWORD")

        with mock.patch(
            "builtins.input",
            side_effect=["R", host, port, user, password, db],
        ):
            result = await cli.config_postgres(settings)
            # should be no exceptions
            self.assertTrue(result)

    async def test_config_redis(self):
        # 1. normla case
        settings = {}
        with mock.patch("builtins.input", side_effect=["localhost", "6379", ""]):
            with mock.patch("omega.cli.check_redis"):
                expected = "redis://localhost:6379"
                await cli.config_redis(settings)
                self.assertEqual(expected, settings["redis"]["dsn"])

        # 2. exception case
        settings = {}
        with mock.patch("builtins.input", side_effect=["localserver", "3180", "", "C"]):
            with mock.patch("omega.cli.logger.exception"):
                await cli.config_redis(settings)
                self.assertDictEqual({}, settings)

    @pytest.mark.skip()
    async def test_setup(self):
        # clear cache to simulate first setup
        redis = await aioredis.create_redis("redis://localhost:6379")
        await redis.flushall()
        redis.close()
        await redis.wait_closed()

        # backup configuration files
        origin = os.path.join(get_config_dir(), "defaults.yaml")
        bak = os.path.join(get_config_dir(), "defaults.bak")

        def save_config(settings):
            os.rename(origin, bak)
            with open(origin, "w") as f:
                f.writelines(self.yaml_dumps(settings))

        with mock.patch("omega.cli.save_config", save_config):
            with mock.patch(
                "builtins.input",
                side_effect=[
                    "/var/log/zillionare/",  # logging
                    os.environ.get("JQ_ACCOUNT"),
                    os.environ.get("JQ_PASSWORD"),
                    "1",
                    "n",  # config no more account
                    os.environ.get("REDIS_HOST"),
                    os.environ.get("REDIS_PORT"),
                    "",  # redis password
                    "",  # continue on postgres config
                    os.environ.get("POSTGRES_HOST"),
                    os.environ.get("POSTGRES_PORT"),
                    os.environ.get("POSTGRES_USER"),
                    os.environ.get("POSTGRES_PASSWORD"),
                    os.environ.get("POSTGRES_DB"),
                ],
            ):
                try:
                    await cli.setup(force=True)
                finally:
                    os.remove(origin)
                    os.rename(bak, origin)

                    # setup has started servers
                    print("stopping omega servers")
                    await cli.stop()
