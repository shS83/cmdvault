"""Square neon tubes with subtle, stationary, irregular local flicker."""
from functools import lru_cache
import math
import random

LOOP_SECONDS = 6


def square_perimeter(left: float, top: float, right: float, bottom: float,
                     spacing: float = 2) -> tuple:
    """Closed rectangle: exact 90-degree joins, no diagonal corner segments."""
    points = []
    corners = ((left,top),(right,top),(right,bottom),(left,bottom),(left,top))
    for start,end in zip(corners,corners[1:]):
        steps = max(1,math.ceil(math.dist(start,end)/spacing))
        points.extend((start[0]+(end[0]-start[0])*i/steps,
                       start[1]+(end[1]-start[1])*i/steps) for i in range(steps))
    points.append(points[0])
    return tuple(points)


@lru_cache(maxsize=16)
def flicker_events(seed: int = 0) -> tuple:
    """Seeded for cacheability; each event has an independent place and time."""
    rng = random.Random(seed)
    return tuple((rng.random(),rng.random(),rng.uniform(.018,.055),
                  rng.uniform(.018,.05),rng.choice((-.22,-.12,.14,.23)))
                 for _ in range(18))


def active_flicker(phase: float, seed: int = 0) -> tuple:
    events = []
    for position,when,duration,width,amplitude in flicker_events(seed):
        distance = (phase-when+.5)%1-.5
        weight = amplitude*math.exp(-(distance/duration)**2)
        if abs(weight) > .002:
            events.append((position,width,weight))
    return tuple(events)


def tube_level(position: float, events: tuple) -> float:
    level = .55
    for center,width,weight in events:
        distance = (position-center+.5)%1-.5
        level += weight*math.exp(-(distance/width)**2)
    return max(.28,min(.85,level))


@lru_cache(maxsize=8)
def neon_asset(width: int, height: int, glow: float, frame: int = 0,
               frames: int = 24, *, inset_x: float = 8, inset_y: float = 8,
               radius: float = 0, seed: int = 0):
    from PIL import Image, ImageDraw, ImageFilter
    width, height = max(32,width), max(32,height)
    # Antialias the tube, preserving hard rectangular geometry at each join.
    scale = 2
    size = (width*scale,height*scale)
    path = square_perimeter(inset_x*scale,inset_y*scale,
                            (width-1-inset_x)*scale,(height-1-inset_y)*scale)
    tube = Image.new('L',size)
    core = Image.new('L',size)
    draw, bright = ImageDraw.Draw(tube), ImageDraw.Draw(core)
    events = active_flicker(frame/max(1,frames),seed)
    for i in range(len(path)-1):
        level = tube_level(i/(len(path)-1),events)
        draw.line((path[i],path[i+1]),fill=round(255*level*glow),width=scale*2)
        bright.line((path[i],path[i+1]),fill=round(255*level),width=scale)
    tube = tube.resize((width,height),Image.Resampling.LANCZOS)
    core = core.resize((width,height),Image.Resampling.LANCZOS)
    result = Image.new('RGBA',(width,height))
    for blur,gain in ((4,1.5),(1.8,1),(0,1)):
        alpha = tube.filter(ImageFilter.GaussianBlur(blur)) if blur else tube
        alpha = alpha.point(lambda value: min(255,round(value*gain)))
        layer = Image.new('RGBA',(width,height),(180,28,255,0))
        layer.putalpha(alpha)
        result = Image.alpha_composite(result,layer)
    layer = Image.new('RGBA',(width,height),(245,220,255,0))
    layer.putalpha(core)
    return Image.alpha_composite(result,layer)
