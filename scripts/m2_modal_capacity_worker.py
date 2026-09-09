"""Locked dependency worker executed inside the Modal capacity Function."""

import gc
import hashlib
from importlib.metadata import version
import json
import os
import platform
from pathlib import Path

import peft
import torch
import transformers
import vllm
from huggingface_hub import snapshot_download
from peft import LoraConfig, get_peft_model
from transformers import AutoModelForCausalLM, AutoTokenizer
from vllm import LLM, SamplingParams


MODEL_ID = "Qwen/Qwen3-4B"
MODEL_REVISION = "1cfa9a7208912126459214e8b04321603b3df60c"
ROOT = Path("/vol/canaries/modal-capacity-v1")


def adapter_digest(model, receipt: dict) -> str:
    digest = hashlib.sha256()
    count = 0
    for name, parameter in model.named_parameters():
        if parameter.requires_grad:
            digest.update(name.encode("utf-8"))
            digest.update(parameter.detach().float().cpu().numpy().tobytes())
            count += parameter.numel()
    if count <= 0:
        raise RuntimeError("no trainable adapter parameters")
    receipt["trainable_adapter_parameters"] = count
    return digest.hexdigest()


def main() -> None:
    receipt = {"schema_version": 1, "phase": "starting", "provider": "modal",
               "gpu_type": "L40S", "model_id": MODEL_ID,
               "model_revision": MODEL_REVISION}
    try:
        expected = {"torch": "2.10.0", "vllm": "0.19.0", "transformers": "5.3.0",
                    "peft": "0.18.1", "ray": "2.51.1", "modal": "1.4.1",
                    "harbor": "0.4.0"}
        observed = {"torch": str(torch.__version__).split("+")[0],
                    "vllm": str(vllm.__version__),
                    "transformers": str(transformers.__version__),
                    "peft": str(peft.__version__), "ray": version("ray"),
                    "modal": version("modal"), "harbor": version("harbor")}
        if observed != expected:
            raise RuntimeError("locked runtime version mismatch")
        if not torch.cuda.is_available() or torch.cuda.device_count() != 1:
            raise RuntimeError("capacity canary requires exactly one CUDA GPU")
        if "L40S" not in torch.cuda.get_device_name(0):
            raise RuntimeError("capacity canary did not receive the registered L40S")

        model_path = snapshot_download(repo_id=MODEL_ID, revision=MODEL_REVISION,
                                       local_dir=f"/vol/models/Qwen3-4B-{MODEL_REVISION}")
        sampling = SamplingParams(temperature=0.0, max_tokens=16)
        llm = LLM(model=model_path, dtype="bfloat16", max_model_len=2048,
                  gpu_memory_utilization=0.45, enforce_eager=True, enable_sleep_mode=True)
        before = llm.generate(["Reply with the word ready."], sampling)[0]
        if not before.outputs or not before.outputs[0].token_ids:
            raise RuntimeError("pre-sleep inference returned no tokens")
        free_before_sleep, _ = torch.cuda.mem_get_info()
        llm.sleep(level=1)
        free_after_sleep, _ = torch.cuda.mem_get_info()
        if free_after_sleep <= free_before_sleep:
            raise RuntimeError("vLLM sleep did not release measurable GPU memory")

        tokenizer = AutoTokenizer.from_pretrained(model_path)
        policy = AutoModelForCausalLM.from_pretrained(
            model_path, torch_dtype=torch.bfloat16, device_map={"": 0})
        policy = get_peft_model(policy, LoraConfig(
            r=32, lora_alpha=64, target_modules="all-linear", task_type="CAUSAL_LM"))
        pre_digest = adapter_digest(policy, receipt)
        batch = tokenizer("Capacity canary update.", return_tensors="pt")
        input_ids = batch["input_ids"].to("cuda")
        optimizer = torch.optim.AdamW(
            [parameter for parameter in policy.parameters() if parameter.requires_grad],
            lr=1e-5, weight_decay=0.0)
        loss = policy(input_ids=input_ids, labels=input_ids).loss
        if not torch.isfinite(loss):
            raise RuntimeError("non-finite canary loss")
        loss_value = float(loss.detach())
        loss.backward()
        gradient_norm = torch.nn.utils.clip_grad_norm_(
            [parameter for parameter in policy.parameters() if parameter.requires_grad], 1.0)
        if not torch.isfinite(gradient_norm) or float(gradient_norm) <= 0:
            raise RuntimeError("non-finite or zero canary adapter gradient")
        gradient_norm_value = float(gradient_norm)
        optimizer.step()
        post_digest = adapter_digest(policy, receipt)
        if post_digest == pre_digest:
            raise RuntimeError("canary adapter parameters did not change")
        adapter_path = ROOT / "adapter"
        policy.save_pretrained(adapter_path)
        del optimizer, loss, policy, tokenizer, batch, input_ids
        gc.collect()
        torch.cuda.empty_cache()

        llm.wake_up()
        after = llm.generate(["Reply with the word awake."], sampling)[0]
        if not after.outputs or not after.outputs[0].token_ids:
            raise RuntimeError("post-wake inference returned no tokens")
        receipt.update({
            "phase": "complete", "python": platform.python_version(),
            "runtime": observed, "device_name": torch.cuda.get_device_name(0),
            "gpu_memory_bytes": torch.cuda.get_device_properties(0).total_memory,
            "sleep_freed_bytes": free_after_sleep - free_before_sleep,
            "pre_inference_tokens": len(before.outputs[0].token_ids),
            "post_inference_tokens": len(after.outputs[0].token_ids),
            "loss": loss_value, "gradient_norm": gradient_norm_value,
            "adapter_digest_before": pre_digest,
            "adapter_digest_after": post_digest, "adapter_path": str(adapter_path),
        })
        del llm
        gc.collect()
        torch.cuda.empty_cache()
    except Exception as exc:
        receipt.update(phase="failed", error_type=type(exc).__name__)

    result = ROOT / "result.json"
    with result.open("x", encoding="utf-8") as handle:
        json.dump(receipt, handle, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    print(json.dumps(receipt, sort_keys=True))


if __name__ == "__main__":
    main()
