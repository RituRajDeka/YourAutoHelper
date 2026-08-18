import logging
from typing import List, Tuple, Dict, Any
from pathlib import Path
from .edit_plan import EditPlan, Cut, Zoom, SpeedChange, SoundEffect, MusicChange

logger = logging.getLogger("ai_video_clipper.plan_executor")

def get_composed_duration(edit_plan: EditPlan, source_duration: float) -> float:
    """
    Computes the final composed video duration after applying cuts and speed changes.
    """
    if not edit_plan.cuts:
        edit_plan.cuts = [Cut(start_time=0.0, end_time=source_duration)]
        
    boundaries = {0.0, source_duration}
    for cut in edit_plan.cuts:
        boundaries.add(cut.start_time)
        boundaries.add(cut.end_time)
    for zoom in edit_plan.zooms:
        boundaries.add(zoom.time)
        boundaries.add(zoom.time + zoom.duration)
    for sc in edit_plan.speed_changes:
        boundaries.add(sc.start_time)
        boundaries.add(sc.end_time)
        
    sorted_boundaries = sorted(list(boundaries))
    total_duration = 0.0
    
    for i in range(len(sorted_boundaries) - 1):
        start = sorted_boundaries[i]
        end = sorted_boundaries[i+1]
        
        # Check if segment is in cuts
        in_cut = False
        for cut in edit_plan.cuts:
            if start >= cut.start_time - 0.001 and end <= cut.end_time + 0.001:
                in_cut = True
                break
        if not in_cut:
            continue
            
        # Determine speed
        active_speed = 1.0
        for sc in edit_plan.speed_changes:
            if start >= sc.start_time - 0.001 and end <= sc.end_time + 0.001:
                active_speed = sc.speed
                break
                
        total_duration += (end - start) / active_speed
        
    return total_duration

def map_transcript_to_composed(words: List[dict], edit_plan: EditPlan, source_duration: float) -> List[dict]:
    """
    Maps word timestamps from the source video timeline to the composed video timeline,
    discarding words in cut/omitted sections.
    """
    if not edit_plan.cuts:
        edit_plan.cuts = [Cut(start_time=0.0, end_time=source_duration)]
        
    boundaries = {0.0, source_duration}
    for cut in edit_plan.cuts:
        boundaries.add(cut.start_time)
        boundaries.add(cut.end_time)
    for zoom in edit_plan.zooms:
        boundaries.add(zoom.time)
        boundaries.add(zoom.time + zoom.duration)
    for sc in edit_plan.speed_changes:
        boundaries.add(sc.start_time)
        boundaries.add(sc.end_time)
        
    sorted_boundaries = sorted(list(boundaries))
    segments = []
    
    for i in range(len(sorted_boundaries) - 1):
        start = sorted_boundaries[i]
        end = sorted_boundaries[i+1]
        
        in_cut = False
        for cut in edit_plan.cuts:
            if start >= cut.start_time - 0.001 and end <= cut.end_time + 0.001:
                in_cut = True
                break
        if not in_cut:
            continue
            
        active_speed = 1.0
        for sc in edit_plan.speed_changes:
            if start >= sc.start_time - 0.001 and end <= sc.end_time + 0.001:
                active_speed = sc.speed
                break
                
        segments.append({
            "start": start,
            "end": end,
            "speed": active_speed
        })
        
    # Build output timeline offsets
    accumulated_out = 0.0
    for seg in segments:
        seg["out_start"] = accumulated_out
        seg["out_duration"] = (seg["end"] - seg["start"]) / seg["speed"]
        accumulated_out += seg["out_duration"]
        
    mapped_words = []
    for w in words:
        w_start = w["start"]
        w_end = w["end"]
        
        start_mapped = None
        end_mapped = None
        
        for seg in segments:
            if seg["start"] - 0.001 <= w_start <= seg["end"] + 0.001:
                offset = max(0.0, w_start - seg["start"])
                start_mapped = seg["out_start"] + offset / seg["speed"]
                break
                
        for seg in segments:
            if seg["start"] - 0.001 <= w_end <= seg["end"] + 0.001:
                offset = max(0.0, w_end - seg["start"])
                end_mapped = seg["out_start"] + offset / seg["speed"]
                break
                
        if start_mapped is not None and end_mapped is not None:
            mapped_words.append({
                "word": w["word"],
                "start": start_mapped,
                "end": end_mapped
            })
            
    return mapped_words

def build_composition_filter(
    edit_plan: EditPlan,
    source_duration: float,
    video_w: int,
    video_h: int,
    has_mask: bool = False,
    sfx_paths: List[str] = None
) -> Tuple[str, str, str]:
    """
    Builds the FFmpeg filter complex string for composing cuts, zooms, and speed changes,
    and mixing sound effects.
    
    Returns:
        filter_complex_str (str): The filter graph stages.
        video_out_label (str): The label of the final composed video stream.
        audio_out_label (str): The label of the final composed audio stream.
    """
    if sfx_paths is None:
        sfx_paths = []
        
    # 1. Determine boundaries in source video timeline
    boundaries = {0.0, source_duration}
    
    # Add cut boundaries
    if edit_plan.cuts:
        for cut in edit_plan.cuts:
            boundaries.add(cut.start_time)
            boundaries.add(cut.end_time)
    else:
        # If no cuts, default to keeping the entire video
        edit_plan.cuts = [Cut(start_time=0.0, end_time=source_duration)]
        
    # Add zoom boundaries
    for zoom in edit_plan.zooms:
        boundaries.add(zoom.time)
        boundaries.add(zoom.time + zoom.duration)
        
    # Add speed change boundaries
    for sc in edit_plan.speed_changes:
        boundaries.add(sc.start_time)
        boundaries.add(sc.end_time)
        
    # Sort and filter boundaries to kept intervals
    sorted_boundaries = sorted(list(boundaries))
    segments = []
    
    for i in range(len(sorted_boundaries) - 1):
        start = sorted_boundaries[i]
        end = sorted_boundaries[i+1]
        
        # Check if this segment is within any cut
        in_cut = False
        for cut in edit_plan.cuts:
            if start >= cut.start_time - 0.001 and end <= cut.end_time + 0.001:
                in_cut = True
                break
                
        if not in_cut:
            continue
            
        # Determine zoom for this segment
        active_zoom = None
        for zoom in edit_plan.zooms:
            if start >= zoom.time - 0.001 and end <= (zoom.time + zoom.duration) + 0.001:
                active_zoom = zoom
                break
                
        # Determine speed change for this segment
        active_speed = 1.0
        for sc in edit_plan.speed_changes:
            if start >= sc.start_time - 0.001 and end <= sc.end_time + 0.001:
                active_speed = sc.speed
                break
                
        segments.append({
            "start": start,
            "end": end,
            "zoom": active_zoom,
            "speed": active_speed
        })
        
    if not segments:
        # Fallback to single full segment
        segments = [{"start": 0.0, "end": source_duration, "zoom": None, "speed": 1.0}]

    stages = []
    concat_inputs = []
    
    # 2. Build filters for each segment
    for idx, seg in enumerate(segments):
        start, end = seg["start"], seg["end"]
        v_in = "0:v"
        a_in = "0:a"
        v_out = f"v_seg_{idx}"
        a_out = f"a_seg_{idx}"
        
        # Trim filters
        v_filters = f"trim=start={start:.3f}:end={end:.3f},setpts=PTS-STARTPTS"
        a_filters = f"atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS"
        
        # Apply Zoom (crop + scale back to target) or default scale + crop to target aspect ratio
        if seg["zoom"] is not None:
            z = seg["zoom"]
            # Crop dimensions
            cw = f"iw/{z.scale:.3f}"
            ch = f"ih/{z.scale:.3f}"
            # Crop offsets with coordinates x, y clamped within safety boundaries
            cx = f"max(0\\,min({z.x:.3f}*iw-iw/{z.scale:.3f}/2\\,iw-iw/{z.scale:.3f}))"
            cy = f"max(0\\,min({z.y:.3f}*ih-ih/{z.scale:.3f}/2\\,iw-iw/{z.scale:.3f}))"
            v_filters += f",crop=w={cw}:h={ch}:x={cx}:y={cy},scale={video_w}:{video_h},setsar=1"
        else:
            v_filters += f",scale={video_w}:{video_h}:force_original_aspect_ratio=increase,crop={video_w}:{video_h},setsar=1"
            
        # Apply Speed
        if seg["speed"] != 1.0:
            sp = seg["speed"]
            v_filters += f",setpts=PTS/{sp:.3f}"
            a_filters += f",atempo={sp:.3f}"
            
        stages.append(f"[{v_in}]{v_filters}[{v_out}]")
        stages.append(f"[{a_in}]{a_filters}[{a_out}]")
        concat_inputs.append(f"[{v_out}][{a_out}]")
        
    # 3. Concatenate all segments
    n_segs = len(segments)
    concat_v = "v_concat"
    concat_a = "a_concat"
    concat_inputs_str = "".join(concat_inputs)
    stages.append(f"{concat_inputs_str}concat=n={n_segs}:v=1:a=1[{concat_v}][{concat_a}]")
    
    video_out = concat_v
    audio_out = concat_a
    
    # 4. Mix sound effects if any
    if edit_plan.sound_effects and sfx_paths:
        mixed_a = "a_sfx_mixed"
        sfx_mix_inputs = [f"[{audio_out}]"]
        
        # SFX files start after source video (and mask if present)
        sfx_start_idx = 2 if has_mask else 1
        
        for idx, sfx in enumerate(edit_plan.sound_effects):
            sfx_in_idx = sfx_start_idx + idx
            sfx_out = f"sfx_delay_{idx}"
            
            # Delay in milliseconds
            delay_ms = int(sfx.time * 1000)
            stages.append(f"[{sfx_in_idx}:a]volume={sfx.volume:.3f},adelay={delay_ms}|{delay_ms}[{sfx_out}]")
            sfx_mix_inputs.append(f"[{sfx_out}]")
            
        n_mix = len(sfx_mix_inputs)
        sfx_mix_inputs_str = "".join(sfx_mix_inputs)
        stages.append(f"{sfx_mix_inputs_str}amix=inputs={n_mix}:duration=first:normalize=0[{mixed_a}]")
        audio_out = mixed_a
        
    return ";".join(stages), video_out, audio_out
