#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Interface for quotes fetcher
"""
from abc import ABC

import numpy
from arrow import Arrow
from omicron.core import FrameType


class QuotesFetcher(ABC):
    async def get_security_list(self) -> numpy.ndarray:
        """
        fetch security list from server. The returned list is a numpy.ndarray, which each elements
        should look like:
                            display_name 	name 	start_date 	end 	type
        000001.XSHE 	平安银行 	PAYH 	1991-04-03 	2200-01-01 	stock
        000002.XSHE 	万科A 	    WKA 	1991-01-29 	2200-01-01 	stock
        Returns:

        """
        raise NotImplementedError

    async def get_bars(self, sec: str, end: Arrow, n_bars: int, frame_type: FrameType) -> numpy.ndarray:

        """
        fetch quotes of sec. Return a numpy rec array with n_bars length, and last frame is end
        Args:
            sec:
            end:
            n_bars:
            frame_type:

        Returns:
            a numpy.ndarray, with each element is:
            'frame': datetime.date or datetime.datetime, depends on frame_type. Denotes which time frame the data
            belongs .
            'open, high, low, close': float
            'volume': double
            'amount': the buy/sell amount in total, double
            'factor': float, may exist or not
        """
        raise NotImplementedError

    async def create_instance(self, **kwargs):
        raise NotImplementedError
