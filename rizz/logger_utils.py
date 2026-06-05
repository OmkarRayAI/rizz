import json
from collections import defaultdict

import numpy as np
import os
from datetime import datetime, timezone
from typing import List

# Global variable to toggle logging
LOG_ENABLED = True


class Logger:
    def __init__(self) -> None:
        self._latency_dict = defaultdict(list)
        self._answer_dict = defaultdict(list)
        self._label_dict = defaultdict(list)

    def log(self, latency: float, answer: str, label: str, key: str) -> None:
        self._latency_dict[key].append(latency)
        self._answer_dict[key].append(answer)
        self._label_dict[key].append(label)

    def _get_mean_latency(self, key: str) -> float:
        latency_array = np.array(self._latency_dict[key])
        return latency_array.mean(), latency_array.std()

    def _get_accuracy(self, key: str) -> float:
        answer_array = np.array(self._answer_dict[key])
        label_array = np.array(self._label_dict[key])
        return (answer_array == label_array).mean()

    def get_results(self, key: str) -> dict:
        mean_latency, std_latency = self._get_mean_latency(key)
        accuracy = self._get_accuracy(key)
        return {
            "mean_latency": mean_latency,
            "std_latency": std_latency,
            "accuracy": accuracy,
        }

    def save_result(self, key: str, path: str):
        with open(f"{path}/dev_react_results.csv", "w") as f:
            for i in range(len(self._answer_dict[key])):
                f.write(f"{self._answer_dict[key][i]},{self._latency_dict[key][i]}\n")


def get_logger() -> Logger:
    return Logger()


# Custom print function to toggle logging


def enable_logging(enable=True):
    """Toggle logging on or off based on the given argument."""
    global LOG_ENABLED
    LOG_ENABLED = enable


def log(*args, block=False, **kwargs):
    """Print the given string only if logging is enabled."""
    if LOG_ENABLED:
        if block:
            print("=" * 80)
        print(*args, **kwargs)
        if block:
            print("=" * 80)


def flush_results(save_path, results):
    print("Saving results")
    json.dump(results, open(save_path, "w"), indent=4)

def log_task_execution( tasks, final_answer):
    # Get current UTC time with ISO format
    filename = "task_execution"
    timestamp = datetime.now(timezone.utc).isoformat()
    data = []

    # Collect task details
    for task_id, task in tasks.items():
        task_info = {
            "task_id": task.idx,
            "name": task.name,
            "dependencies": list(task.dependencies),
            "args": list(task.args),
            "thought": task.thought,
            "observation": task.observation,
            "is_join": task.is_join,
        }
        data.append(task_info)

    # Prepare log data
    log_data = {
        "timestamp": timestamp,
        "final_answer": final_answer,
        "data": data
    }

    # Ensure log directory exists
    log_dir = os.path.abspath(os.path.join(os.getcwd(), "..", "logs"))
    os.makedirs(log_dir, exist_ok=True)

    # Generate filename
    log_filename = f"{timestamp.replace(':', '_')}_{filename}.json"
    filepath = os.path.join(log_dir, log_filename)

    # Write log to file
    try:
        with open(filepath, 'w') as log_file:
            json.dump(log_data, log_file, indent=4)
        print(f"Log written to {filepath}")
    except Exception as e:
        print(f"Error writing log file: {e}")

