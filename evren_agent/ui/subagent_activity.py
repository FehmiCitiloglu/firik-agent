"""Live and historical subagent cards share the same presentation."""


def update_subagent_activity(activities, metadata, content="", historical=False):
    identifier = metadata["agent_id"]
    activity = next((item for item in activities if item.get("agent_id") == identifier), None)
    if activity is None:
        role = "Araştırma" if metadata.get("role") == "research" else "İnceleme"
        activity = {"tool": f"Alt ajan · {role}", "agent_id": identifier,
                    "arguments": {"görev": metadata.get("task", "")}, "output": ""}
        activities.append(activity)
    activity.update(status={"running": "Çalışıyor", "completed": "Tamamlandı",
                            "failed": "Hata", "cancelled": "Kesildi"}.get(metadata.get("status"), "Kesildi"),
                    duration_ms=metadata.get("elapsed_sec", 0) * 1000, historical=historical)
    if metadata.get("status") != "running":
        activity["output"] = content
    elif metadata.get("tool_name"):
        activity["output"] = "Etkin araç: " + metadata["tool_name"]
    return activity
