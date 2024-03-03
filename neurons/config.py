# The MIT License (MIT)
# Copyright © 2023 Yuma Rao
# Copyright © 2023 Opentensor Foundation

# Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
# documentation files (the “Software”), to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in all copies or substantial portions of
# the Software.

# THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO
# THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
# THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.

from enum import auto
import enum
from math import e
import os
import argparse
from pathlib import Path
import bittensor as bt
from loguru import logger

from common import utils

from dotenv import load_dotenv

load_dotenv()


def check_config(config: bt.config):
    r"""Checks/validates the config namespace object."""
    bt.logging.check_config(config)

    full_path = os.path.expanduser(
        "{}/{}/{}/netuid{}/{}".format(
            config.logging.logging_dir,  # TODO: change from ~/.bittensor/miners to ~/.bittensor/neurons
            config.wallet.name,
            config.wallet.hotkey,
            config.netuid,
            config.neuron.name,
        )
    )

    config.neuron.full_path = os.path.expanduser(full_path)
    if not os.path.exists(config.neuron.full_path):
        os.makedirs(config.neuron.full_path, exist_ok=True)

    if not config.neuron.dont_save_events:
        # Add custom event logger for the events.
        try:
            # Check if the level is already configured. If it isn't, a ValueError is raised.
            logger.level("EVENTS")
        except ValueError:
            logger.level("EVENTS", no=38, icon="📝")
            logger.add(
                os.path.join(config.neuron.full_path, "events.log"),
                rotation=config.neuron.events_retention_size,
                serialize=True,
                enqueue=True,
                backtrace=False,
                diagnose=False,
                level="EVENTS",
                format="{time:YYYY-MM-DD at HH:mm:ss} | {level} | {message}",
            )


class NeuronType(enum.Enum):
    MINER = auto()
    VALIDATOR = auto()


def add_args(neuron_type: NeuronType, parser):
    """
    Adds relevant arguments to the parser for operation.
    """
    # Netuid Arg: The netuid of the subnet to connect to.
    parser.add_argument("--netuid", type=int, help="Subnet netuid", default=13)

    parser.add_argument(
        "--neuron.epoch_length",
        type=int,
        help="The default epoch length (how often we sync the metagraph, measured in 12 second blocks).",
        default=100,
    )

    parser.add_argument(
        "--neuron.events_retention_size",
        type=str,
        help="Events retention size.",
        default="2 GB",
    )

    parser.add_argument(
        "--neuron.dont_save_events",
        action="store_true",
        help="If set, we dont save events to a log file.",
        default=False,
    )

    if neuron_type == NeuronType.VALIDATOR:
        parser.add_argument(
            "--neuron.axon_off",
            "--axon_off",
            action="store_true",
            # Note: the validator needs to serve an Axon with their IP or they may
            #   be blacklisted by the firewall of serving peers on the network.
            help="Set this flag to not attempt to serve an Axon.",
            default=False,
        )
        parser.add_argument(
            "--wandb.off",
            action="store_true",
            help="Set this flag to disable logging to wandb.",
            default=False,
        )

    elif neuron_type == NeuronType.MINER:
        parser.add_argument(
            "--neuron.database_name",
            type=str,
            help="The name of the database.",
            default="SqliteMinerStorage.sqlite",
        )

        parser.add_argument(
            "--neuron.max_database_size_gb_hint",
            type=int,
            help="Hint for the size of the database to target in GBs. Expect additional some additional overhead.",
            # We intentionally choose a large default to avoid Miner's accidentally deleting data when they
            # run with the default value.
            default=250,
        )

        root_dir = Path(os.path.dirname(__file__)).parent
        default_file = os.path.join(
            os.path.join(root_dir, "scraping/config/scraping_config.json"),
        )
        parser.add_argument(
            "--neuron.scraping_config_file",
            type=str,
            help="The location of the scraping config JSON file to use",
            default=default_file,
        )

        parser.add_argument(
            "--offline",
            action="store_true",
            help="Set this flag to true to run the miner in offline mode.",
            default=False,
        )
    else:
        raise ValueError(f"Invalid neuron type: {neuron_type}")


def create_config(neuron_type: NeuronType):
    """
    Returns the configuration for the NeuronType
    """
    parser = argparse.ArgumentParser()
    bt.wallet.add_args(parser)
    bt.subtensor.add_args(parser)
    bt.logging.add_args(parser)
    bt.axon.add_args(parser)
    add_args(neuron_type, parser)

    return bt.config(parser)
