from importlib import import_module as _import_module

_SUBMODULES = frozenset(
    {
        "local_asr_diagnostics",
        "local_asr_gpu_provisioning",
        "local_asr_readiness",
        "local_asr_selection",
    }
)
_EXPORT_SOURCES = {
    "local_asr_status_for_provider": "local_asr_selection",
    "LocalASRBasicLogSink": "local_asr_diagnostics",
    "LocalASRDiagnosticLogSink": "local_asr_diagnostics",
    "LocalASRDiagnosticsGpuEffect": "local_asr_diagnostics",
    "LocalASRDiagnosticsGpuEffectSink": "local_asr_diagnostics",
    "LocalASRDiagnosticsOwner": "local_asr_diagnostics",
    "LocalASRGpuActivationRetry": "local_asr_gpu_provisioning",
    "LocalASRGpuDiscoveryOriginProvider": "local_asr_diagnostics",
    "LocalASRGpuProvisioningDiagnostic": "local_asr_gpu_provisioning",
    "LocalASRGpuProvisioningDiagnosticSink": "local_asr_gpu_provisioning",
    "LocalASRGpuProvisioningEffect": "local_asr_gpu_provisioning",
    "LocalASRGpuProvisioningEffectSink": "local_asr_gpu_provisioning",
    "LocalASRGpuProvisioningOwner": "local_asr_gpu_provisioning",
    "LocalASRGpuProvisioningState": "local_asr_gpu_provisioning",
    "LocalASRGpuProvisioningStateProvider": "local_asr_gpu_provisioning",
    "LocalASRGpuProvisioningUiState": "local_asr_gpu_provisioning",
    "LocalASRProvisioningProvider": "local_asr_gpu_provisioning",
    "LocalASRReadinessChannel": "local_asr_readiness",
    "LocalASRReadinessGpuPendingSink": "local_asr_readiness",
    "LocalASRReadinessGpuStateProvider": "local_asr_readiness",
    "LocalASRReadinessGpuValidator": "local_asr_readiness",
    "LocalASRReadinessOwner": "local_asr_readiness",
    "LocalASRReadinessState": "local_asr_readiness",
    "LocalASRReadinessStateProvider": "local_asr_readiness",
    "LocalASRSelectionDecision": "local_asr_selection",
    "required_local_asr_model_ids": "local_asr_selection",
    "resolve_local_asr_selection": "local_asr_selection",
}

__all__ = [
    "local_asr_diagnostics",
    "local_asr_gpu_provisioning",
    "local_asr_readiness",
    "local_asr_selection",
    "local_asr_status_for_provider",
    "LocalASRBasicLogSink",
    "LocalASRDiagnosticLogSink",
    "LocalASRDiagnosticsGpuEffect",
    "LocalASRDiagnosticsGpuEffectSink",
    "LocalASRDiagnosticsOwner",
    "LocalASRGpuActivationRetry",
    "LocalASRGpuDiscoveryOriginProvider",
    "LocalASRGpuProvisioningDiagnostic",
    "LocalASRGpuProvisioningDiagnosticSink",
    "LocalASRGpuProvisioningEffect",
    "LocalASRGpuProvisioningEffectSink",
    "LocalASRGpuProvisioningOwner",
    "LocalASRGpuProvisioningState",
    "LocalASRGpuProvisioningStateProvider",
    "LocalASRGpuProvisioningUiState",
    "LocalASRProvisioningProvider",
    "LocalASRReadinessChannel",
    "LocalASRReadinessGpuPendingSink",
    "LocalASRReadinessGpuStateProvider",
    "LocalASRReadinessGpuValidator",
    "LocalASRReadinessOwner",
    "LocalASRReadinessState",
    "LocalASRReadinessStateProvider",
    "LocalASRSelectionDecision",
    "required_local_asr_model_ids",
    "resolve_local_asr_selection",
]


def __getattr__(name: str) -> object:
    if name in _SUBMODULES:
        return _import_module(f".{name}", __name__)
    source = _EXPORT_SOURCES.get(name)
    if source is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(_import_module(f".{source}", __name__), name)
