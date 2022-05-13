#!/usr/bin/env python
# -*- coding: utf-8 -*-

import datetime
import logging
from typing import Optional

import arrow
import cfg4py
from cfg4py.config import Config

from coretypes import FrameType
from omicron.dal import cache
from omicron.models.timeframe import TimeFrame

from omega.core import constants
from omega.core.events import Events
from omega.master.tasks.calibration_task import daily_calibration_job
from omega.master.tasks.sync_other_bars import sync_min_5_15_30_60, sync_month_bars, sync_week_bars
from omega.master.tasks.sync_price_limit import sync_trade_price_limits
from omega.master.tasks.synctask import BarsSyncTask
from omega.master.tasks.task_utils import abnormal_master_report


logger = logging.getLogger(__name__)
cfg: Config = cfg4py.get_instance()


async def get_after_hour_sync_job_task() -> Optional[BarsSyncTask]:
    """获取盘后同步的task实例"""
    now = arrow.now().naive
    if not TimeFrame.is_trade_day(now):  # pragma: no cover
        logger.info("非交易日，不同步")
        return
    end = TimeFrame.last_min_frame(now, FrameType.MIN1)
    if now < end:  # pragma: no cover
        logger.info("当天未收盘，禁止同步")
        return
    name = "day"

    task = BarsSyncTask(
        event=Events.OMEGA_DO_SYNC_DAY,
        name=name,
        frame_type=[FrameType.MIN1, FrameType.DAY],
        end=end,
        timeout=60 * 60 * 2,
        recs_per_sec=240 * 2 + 4,
    )
    return task


@abnormal_master_report()
async def after_hour_sync_job():
    """交易日盘后同步任务入口

    收盘之后同步今天的日线和分钟线
    """
    task = await get_after_hour_sync_job_task()
    if not task:
        return
    await task.run()
    return task


async def get_sync_minute_date():
    """获取这次同步分钟线的时间和n_bars
    如果redis记录的是11：30
    """
    end = arrow.now().naive.replace(second=0, microsecond=0)
    first = end.replace(hour=9, minute=30, second=0, microsecond=0)
    # 检查当前时间是否在交易时间内
    if not TimeFrame.is_trade_day(end):  # pragma: no cover
        logger.info("非交易日，不同步")
        return False
    if end < first:  # pragma: no cover
        logger.info("时间过早，不能拿到k线数据")
        return False

    end = TimeFrame.floor(end, FrameType.MIN1)
    tail = await cache.sys.get(constants.BAR_SYNC_MINUTE_TAIL)
    # tail = "2022-02-22 13:29:00"
    if tail:
        # todo 如果有，这个时间最早只能是今天的9点31分,因为有可能是昨天执行完的最后一次
        tail = datetime.datetime.strptime(tail, "%Y-%m-%d %H:%M:00")
        if tail < first:
            tail = first
    else:
        tail = first

    # 取上次同步截止时间+1 计算出n_bars
    tail = TimeFrame.floor(tail + datetime.timedelta(minutes=1), FrameType.MIN1)
    n_bars = TimeFrame.count_frames(tail, end, FrameType.MIN1)  # 获取到一共有多少根k线
    return end, n_bars


async def get_sync_minute_bars_task() -> Optional[BarsSyncTask]:
    """构造盘中分钟线的task实例"""
    ret = await get_sync_minute_date()
    if not ret:  # pragma: no cover
        return
    else:
        end, n_bars = ret
    name = "minute"
    timeout = 60
    task = BarsSyncTask(
        event=Events.OMEGA_DO_SYNC_MIN,
        name=name,
        frame_type=[FrameType.MIN1],
        end=end,
        timeout=timeout * n_bars,
        n_bars=n_bars,
        recs_per_sec=n_bars,
    )
    return task


async def run_sync_minute_bars_task(task: BarsSyncTask):
    """执行task的方法"""
    flag = await task.run()
    if flag:
        # 说明正常执行完的
        await cache.sys.set(
            constants.BAR_SYNC_MINUTE_TAIL,
            task.end.strftime("%Y-%m-%d %H:%M:00"),
        )

    return task


@abnormal_master_report()
async def sync_minute_bars():
    """盘中同步每分钟的数据
    1. 从redis拿到上一次同步的分钟数据
    2. 计算开始和结束时间
    """
    task = await get_sync_minute_bars_task()
    if not task:
        return
    await run_sync_minute_bars_task(task)



async def load_cron_task(scheduler):
    scheduler.add_job(
        sync_minute_bars,
        "cron",
        hour=9,
        minute="31-59",
        name=f"{FrameType.MIN1.value}:9:31-59",
    )
    scheduler.add_job(
        sync_minute_bars,
        "cron",
        hour=10,
        minute="*",
        name=f"{FrameType.MIN1.value}:10:*",
    )
    scheduler.add_job(
        sync_minute_bars,
        "cron",
        hour=11,
        # review: 设定30分结束同步，会导致无法同步到30分钟的数据
        minute="0-31",
        name=f"{FrameType.MIN1.value}:11:0-31",
    )
    scheduler.add_job(
        sync_minute_bars,
        "cron",
        hour="13-14",
        # review: 1-59将导致整数点上的分钟线被延时同步
        minute="0-59",
        name=f"{FrameType.MIN1.value}:13-14:*",
    )
    scheduler.add_job(
        sync_minute_bars,
        "cron",
        hour=15,
        # review: 之前的实现会导致只同步到59分钟的数据
        minute="0-1",
        name=f"{FrameType.MIN1.value}:15:00",
    )

    scheduler.add_job(
        after_hour_sync_job,
        "cron",
        hour="15",
        minute=5,
        name="after_hour_sync_job",
    )
    scheduler.add_job(
        sync_week_bars,
        "cron",
        hour=2,
        minute=5,
        name="sync_week_bars",
    )
    scheduler.add_job(
        sync_min_5_15_30_60,
        "cron",
        hour=2,
        minute=30,
        name="sync_min_5_15_30_60",
    )
    scheduler.add_job(
        sync_month_bars,
        "cron",
        hour=2,
        minute=5,
        name="sync_month_bars",
    )
    scheduler.add_job(
        daily_calibration_job,
        "cron",
        hour=2,
        minute=5,
        name="daily_calibration_sync",
    )

    scheduler.add_job(
        sync_trade_price_limits,
        "cron",
        hour=9,
        minute=31,
        name="sync_trade_price_limits",
    )
