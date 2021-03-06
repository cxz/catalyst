#!/usr/bin/env python

import os
import copy
import atexit
import argparse
from pprint import pprint
import multiprocessing as mp
from redis import StrictRedis
import torch

from catalyst.utils.args import parse_args_uargs
from catalyst.utils.misc import set_global_seeds, import_module, boolean_flag
from catalyst.rl.offpolicy.sampler import Sampler
import catalyst.rl.random_process as rp

set_global_seeds(42)
os.environ["OMP_NUM_THREADS"] = "1"
torch.set_num_threads(1)

parser = argparse.ArgumentParser()
parser.add_argument(
    "--config",
    type=str,
    required=True)
parser.add_argument(
    "--algorithm",
    type=str,
    default=None)
parser.add_argument(
    "--environment",
    type=str,
    default=None)
parser.add_argument(
    "--logdir",
    type=str,
    default=None)
parser.add_argument(
    "--resume",
    type=str,
    default=None)
parser.add_argument(
    "--vis",
    type=int,
    default=None)
parser.add_argument(
    "--infer",
    type=int,
    default=None)
parser.add_argument(
    "--train",
    type=int,
    default=None)
parser.add_argument(
    "--action-noise-prob",
    type=float,
    default=None)
parser.add_argument(
    "--param-noise-prob",
    type=float,
    default=None)
parser.add_argument(
    "--max-noise-power",
    type=float,
    default=None)
parser.add_argument(
    "--max-action-noise",
    type=float,
    default=None)
parser.add_argument(
    "--max-param-noise",
    type=float,
    default=None)
boolean_flag(parser, "debug", default=False)

args = parser.parse_args()
args, config = parse_args_uargs(args, [])

env_module = import_module("env_module", args.environment)
algo_module = import_module("algo_module", args.algorithm)


def run_sampler(
        *,
        config, vis, infer,
        action_noise_prob, param_noise_prob,
        action_noise=None, param_noise=None,
        noise_power=None,  # @TODO: remove
        id=None, resume=None, debug=False):
    config_ = copy.deepcopy(config)

    if debug:
        redis_server = None
        redis_prefix = None
    else:
        redis_server = StrictRedis(
            port=config_.get("redis", {}).get("port", 12000))
        redis_prefix = config_.get("redis", {}).get("prefix", "")

    id = id or 0
    set_global_seeds(42 + id)

    action_noise = action_noise or noise_power
    param_noise = param_noise or noise_power

    if "randomized_start" in config_["env"]:
        config_["env"]["randomized_start"] = (
                config_["env"]["randomized_start"] and not infer)
    env = env_module.ENV(**config_["env"], visualize=vis)
    algo_kwargs = algo_module.prepare_for_sampler(config_)

    rp_params = config_.get("random_process", {})
    random_process = rp.__dict__[
        rp_params.pop("random_process", "RandomProcess")]
    rp_params["sigma"] = action_noise
    random_process = random_process(**rp_params)

    seeds = config_.get("seeds", None) \
        if infer \
        else config_.get("train_seeds", None)
    min_episode_steps = config_["sampler"].pop("min_episode_steps", None)
    min_episode_steps = min_episode_steps if not infer else None
    min_episode_reward = config_["sampler"].pop("min_episode_reward", None)
    min_episode_reward = min_episode_reward if not infer else None

    if seeds is not None:
        min_episode_steps = None
        min_episode_reward = None

    pprint(config_["sampler"])
    pprint(algo_kwargs)

    sampler = Sampler(
        **config_["sampler"],
        **algo_kwargs,
        env=env,
        logdir=args.logdir, id=id,
        redis_server=redis_server,
        redis_prefix=redis_prefix,
        mode="infer" if infer else "train",
        random_process=random_process,
        action_noise_prob=action_noise_prob,
        param_noise_prob=param_noise_prob,
        param_noise_d=param_noise,
        seeds=seeds,
        min_episode_steps=min_episode_steps,
        min_episode_reward=min_episode_reward,
        resume=resume)

    pprint(sampler)

    sampler.run()


processes = []
sampler_id = 0


def on_exit():
    for p in processes:
        p.terminate()


atexit.register(on_exit)

# run_sampler(vis=False,
#             infer=False,
#             noise_power=None,
#             action_noise=0.5,
#             param_noise=0.5,
#             action_noise_prob=args.action_noise_prob,
#             param_noise_prob=args.param_noise_prob,
#             config=config,
#             id=sampler_id,
#             resume=args.resume)

for i in range(args.vis):
    p = mp.Process(
        target=run_sampler,
        kwargs=dict(
            vis=True,
            infer=True,
            noise_power=0,
            action_noise_prob=0,
            param_noise_prob=0,
            config=config,
            id=sampler_id,
            resume=args.resume,
            debug=args.debug))
    p.start()
    processes.append(p)
    sampler_id += 1

for i in range(args.infer):
    p = mp.Process(
        target=run_sampler,
        kwargs=dict(
            vis=False,
            infer=True,
            noise_power=0,
            action_noise_prob=0,
            param_noise_prob=0,
            config=config,
            id=sampler_id,
            resume=args.resume,
            debug=args.debug))
    p.start()
    processes.append(p)
    sampler_id += 1

for i in range(1, args.train + 1):
    noise_power = args.max_noise_power * i / args.train \
        if args.max_noise_power is not None \
        else None
    action_noise = args.max_action_noise * i / args.train \
        if args.max_action_noise is not None \
        else None
    param_noise = args.max_param_noise * i / args.train \
        if args.max_param_noise is not None \
        else None
    p = mp.Process(
        target=run_sampler,
        kwargs=dict(
            vis=False,
            infer=False,
            noise_power=noise_power,
            action_noise=action_noise,
            param_noise=param_noise,
            action_noise_prob=args.action_noise_prob,
            param_noise_prob=args.param_noise_prob,
            config=config,
            id=sampler_id,
            resume=args.resume,
            debug=args.debug))
    p.start()
    processes.append(p)
    sampler_id += 1

for p in processes:
    p.join()
