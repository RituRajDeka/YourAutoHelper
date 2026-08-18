from typing import List, Optional
from pydantic import BaseModel, Field

class Cut(BaseModel):
    start_time: float = Field(..., ge=0.0, description='Start time of the cut segment in seconds.')
    end_time: float = Field(..., ge=0.0, description='End time of the cut segment in seconds.')

class Zoom(BaseModel):
    time: float = Field(..., ge=0.0, description='Start time of the zoom in seconds.')
    duration: float = Field(..., ge=0.0, description='Duration of the zoom transition/hold in seconds.')
    scale: float = Field(..., gt=0.0, description='Zoom scale factor (e.g., 1.2 for 120% zoom).')
    x: float = Field(..., ge=0.0, le=1.0, description='X coordinate of the zoom center (0.0 to 1.0).')
    y: float = Field(..., ge=0.0, le=1.0, description='Y coordinate of the zoom center (0.0 to 1.0).')

class SpeedChange(BaseModel):
    start_time: float = Field(..., ge=0.0, description='Start time of the speed change in seconds.')
    end_time: float = Field(..., ge=0.0, description='End time of the speed change in seconds.')
    speed: float = Field(..., gt=0.0, description='Playback speed multiplier.')

class Transition(BaseModel):
    cut_index: int = Field(..., ge=0, description='Index of the cut after which the transition occurs.')
    type: str = Field(..., description='Type of transition.')
    duration: float = Field(..., ge=0.0, description='Duration of the transition in seconds.')

class SoundEffect(BaseModel):
    time: float = Field(..., ge=0.0, description='Time index to trigger the sound effect in seconds.')
    name: str = Field(..., description='File name of the sound effect.')
    volume: float = Field(1.0, ge=0.0, le=1.0, description='Volume level of the sound effect (0.0 to 1.0).')

class MusicChange(BaseModel):
    start_time: float = Field(..., ge=0.0, description='Time index to apply the music change in seconds.')
    track: str = Field(..., description='Identifier or filename of the audio track.')
    volume: float = Field(1.0, ge=0.0, le=1.0, description='Target volume level of the track (0.0 to 1.0).')
    duck: bool = Field(False, description='Whether to duck background music during spoken captions.')

class EmphasisPoint(BaseModel):
    time: float = Field(..., ge=0.0, description='Time index to display the emphasis point in seconds.')
    duration: float = Field(..., ge=0.0, description='Duration of the overlay in seconds.')
    type: str = Field(..., description='Type of emphasis point (e.g., text, emoji, sticker).')
    content: str = Field(..., description='Text content, emoji symbol, or sticker identifier.')
    pos_x: float = Field(..., ge=0.0, le=1.0, description='Normalized X coordinate of the overlay position (0.0 to 1.0).')
    pos_y: float = Field(..., ge=0.0, le=1.0, description='Normalized Y coordinate of the overlay position (0.0 to 1.0).')

class CaptionPreferences(BaseModel):
    style_preset: Optional[str] = Field(None, description='Name of a pre-configured style preset.')
    font_family: Optional[str] = Field(None, description='Font family to use for captions.')
    font_size: Optional[int] = Field(None, ge=1, description='Font size in pixels or points.')
    primary_color: Optional[str] = Field(None, description='Hex or name of primary text color.')
    highlight_color: Optional[str] = Field(None, description='Hex or name of highlighted word color.')
    pos_x: Optional[float] = Field(None, ge=0.0, le=1.0, description='Normalized X position for captions.')
    pos_y: Optional[float] = Field(None, ge=0.0, le=1.0, description='Normalized Y position for captions.')
    uppercase: Optional[bool] = Field(None, description='Force text to uppercase.')
    animation: Optional[str] = Field(None, description='Animation effect applied to captions.')

class EditPlan(BaseModel):
    cuts: List[Cut] = Field(default_factory=list)
    zooms: List[Zoom] = Field(default_factory=list)
    speed_changes: List[SpeedChange] = Field(default_factory=list)
    transitions: List[Transition] = Field(default_factory=list)
    sound_effects: List[SoundEffect] = Field(default_factory=list)
    music_changes: List[MusicChange] = Field(default_factory=list)
    emphasis_points: List[EmphasisPoint] = Field(default_factory=list)
    caption_preferences: Optional[CaptionPreferences] = None
