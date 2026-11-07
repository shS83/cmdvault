import base64
import io
import unittest
from unittest.mock import patch
from src.visuals.config import VisualConfig
from src.visuals.detector import Capabilities, detect, select
from src.visuals.ansi import AnsiRenderer, fzf_options
from src.visuals.kitty import KittyRenderer, encode_rgba
from src.visuals.sixel import encode_sixel
from src.visuals.compositor import FrameClock
from src.visuals.diagnostics import run


class Terminal(io.StringIO):
    def isatty(self):
        return True


class VisualTests(unittest.TestCase):
    def test_validation(self):
        for kwargs in ({'glow': -1}, {'glow': float('nan')}, {'fps': 0}, {'renderer': 'bad'}, {'animation': 1}):
            with self.assertRaises(ValueError):
                VisualConfig(**kwargs)
        self.assertEqual(VisualConfig().fps, 30)

    def test_passive_detection(self):
        for env in ({'TERM': 'xterm-kitty'}, {'TERM':'xterm', 'TMUX':'yes'}, {'SSH_CONNECTION':'yes'}):
            caps = detect(Terminal(), env)
            self.assertFalse(caps.kitty)
            self.assertEqual(select('auto', caps)[0], 'ansi')
        self.assertFalse(detect(io.StringIO(), {'COLORTERM':'truecolor'}).truecolor)

    def test_priority_and_layout(self):
        caps = Capabilities(True, 'test', True, True, True)
        self.assertEqual(select('auto', caps, raster_layout=True)[0], 'kitty')
        self.assertEqual(select('sixel', caps, raster_layout=True)[0], 'sixel')
        self.assertEqual(select('kitty', caps)[0], 'ansi')
        self.assertIn('unavailable', select('kitty', caps)[1])

    def test_explicit_sixel_on_windows_terminal(self):
        caps = detect(Terminal(), {'TERM': 'xterm-256color', 'WT_SESSION': 'test'})
        self.assertFalse(caps.sixel)
        self.assertEqual(select('sixel', caps, raster_layout=True), ('sixel', ''))
        self.assertEqual(select('sixel', caps)[0], 'ansi')
        self.assertEqual(select('sixel', detect(io.StringIO(), {}), raster_layout=True)[0], 'ansi')

    def test_sixel_diagnostics_report_selected_backend(self):
        output = Terminal()
        caps = Capabilities(True, 'xterm-256color')
        with patch('src.visuals.diagnostics.SixelRenderer') as renderer:
            run(VisualConfig(renderer='sixel', animation=False), output, caps=caps)
        lines = renderer.return_value.draw.call_args.args[0]
        self.assertIn('Backend: sixel', lines)
        self.assertIn('Explicit SIXEL selection', lines)
        self.assertNotIn('fallback', ' '.join(lines).lower())

    def test_non_tty(self):
        output = io.StringIO()
        run(VisualConfig(), output)
        self.assertNotIn('\x1b', output.getvalue())
        renderer = KittyRenderer(output, VisualConfig(), Capabilities(False,'pipe'))
        with renderer:
            renderer.draw(['plain'])
        self.assertNotIn('\x1b', output.getvalue())

    def test_ansi_resize_and_exception_cleanup(self):
        output = Terminal()
        renderer = AnsiRenderer(output, VisualConfig(), Capabilities(True, 'test', True))
        with self.assertRaises(RuntimeError):
            with renderer:
                for w,h in ((1,1), (4,3), (80,24)):
                    renderer.resize(w,h)
                    renderer.draw(['test'])
                    self.assertEqual(renderer.width,w)
                raise RuntimeError()
        self.assertIn('?25h',output.getvalue())
        self.assertIn('?1049l',output.getvalue())
        self.assertIn('bg+:#302040', fzf_options()[0])

    def test_kitty_chunking_and_scoped_cleanup(self):
        data = bytes(range(256))*32
        encoded = encode_rgba(data, 32,64,123)
        chunks = encoded.split('\x1b_G')[1:]
        payload = ''
        for chunk in chunks:
            control, body = chunk[:-2].split(';')
            self.assertLessEqual(len(body),4096)
            payload += body
        self.assertEqual(base64.b64decode(payload),data)
        self.assertIn('m=0',chunks[-1])
        output = Terminal()
        renderer = KittyRenderer(output, VisualConfig(), Capabilities(True,'test',True,True))
        with renderer:
            pass
        self.assertIn(f'd=I,i={renderer.image_id}',output.getvalue())
        self.assertNotIn('d=A',output.getvalue())

    def test_sixel_pixel_masks(self):
        # White at y=0 and y=5 gives mask 33, SIXEL character 96 (`).
        pixels = b''.join(b'\xff\xff\xff' if y in (0,5) else b'\0\0\0' for y in range(6))
        encoded = encode_sixel(1,6,pixels)
        self.assertTrue(encoded.startswith('\x1bP0;1;0q"1;1;1;6'))
        self.assertIn('#15`', encoded)
        self.assertTrue(encoded.endswith('\x1b\\'))
        self.assertIn('-',encode_sixel(1,7,b'\xff\xff\xff'*7))

    def test_sixel_shared_frame_stays_in_reserved_margins(self):
        from src.visuals.sixel import sixel_frame_packet, sixel_status_packet
        packet = sixel_frame_packet(80, 24, 800, 480)
        self.assertEqual(packet.count('\x1bP0;1;0q'), 4)
        for position in ('1;1', '23;1', '2;1', '2;79'):
            self.assertIn('\x1b[' + position + 'H', packet)
        orb = sixel_status_packet(80, 24, 'synced', 800, 480)
        self.assertIn('STATUS: SYNCED', orb)
        self.assertIn('"1;1;104;104', orb)
        self.assertNotIn('\x1b_G', packet + orb)

    def test_sixel_frame_never_reaches_scrolling_boundary(self):
        import re
        from src.visuals.sixel import sixel_frame_packet, sixel_status_packet
        # 16/20/22px cells expose padding to the next six-pixel band.
        for ch in (16, 20, 22, 24, 30):
            for columns, rows in ((80, 24), (90, 28), (40, 14)):
                packet = sixel_frame_packet(columns, rows, columns*10, rows*ch)
                packet += sixel_status_packet(columns, rows, 'ready', columns*10, rows*ch)
                images = re.findall(r'\x1b\[(\d+);(\d+)H\x1bP0;1;0q"1;1;(\d+);(\d+)(.*?)\x1b\\', packet, re.S)
                self.assertEqual(len(images), 5)
                for row, col, width, height, data in images:
                    # Model SIXEL band consumption, not just the declared size.
                    bands = data.count('-') + 1
                    self.assertEqual(bands, (int(height)+5)//6)
                    end_y = (int(row)-1)*ch + bands*6
                    self.assertLessEqual(end_y, (rows-1)*ch)
                    self.assertLessEqual((int(col)-1)*10 + int(width), columns*10)

    def test_sixel_footer_text_never_erases_bottom_neon_row(self):
        from src.visuals.picker import pulse_background_packet, ansi_status_packet
        from src.visuals.sixel import footer_rows
        import re
        for rows in (14, 24, 28, 40):
            packet = pulse_background_packet(80, rows,
                       reserved_rows=footer_rows(rows), frame_rows=2)
            for state in ('ready', 'syncing', 'synced', 'error', 'offline'):
                packet += ansi_status_packet(80, rows, state, footer_inset=2)
            text_rows = [int(row) for row in re.findall(r'\x1b\[(\d+);\d+H', packet)]
            self.assertTrue(text_rows)
            self.assertGreaterEqual(min(text_rows), rows-footer_rows(rows)+1)
            self.assertLess(max(text_rows), rows-1)

    def test_sixel_encoder_preserves_rgb_palette(self):
        from PIL import Image
        from src.visuals.sixel import encode_image
        encoded = encode_image(Image.new('RGB', (8, 6), (0, 255, 0)))
        self.assertIn(';2;0;100;0', encoded)
        self.assertIn('!8~', encoded)

    def test_terminal_pixel_reports_preserve_keyboard_input(self):
        from src.visuals.geometry import PixelReports
        reports = PixelReports()
        self.assertEqual(reports.feed(b'query\x1b[6;24;'), b'query')
        self.assertEqual(reports.feed(b'12tmore\x1b[4;720;1200t'), b'more')
        self.assertEqual(reports.pixels(100, 30), (1200, 720))
        self.assertEqual(reports.pixels(80, 24), (960, 576))
        self.assertEqual(reports.feed(b'\x1b[A\x1b[B'), b'\x1b[A\x1b[B')
        self.assertEqual(reports.feed(b'\x1b'), b'')
        self.assertEqual(reports.flush(), b'\x1b')
        self.assertEqual(reports.feed(b'\x1b[6;0;0t'), b'')
        self.assertEqual(reports.pixels(80, 24), (960, 576))

    def test_clock(self):
        clock = FrameClock(12)
        self.assertTrue(clock.due(10))
        self.assertFalse(clock.due(10.01))
        self.assertTrue(clock.due(11,idle=True))
        self.assertFalse(clock.due(11.5,idle=True))
        self.assertGreater(clock.delay(11.5),0)

    def test_preferences_round_trip(self):
        from pathlib import Path
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(VisualConfig, 'path', return_value=Path(folder)/'visuals.json'):
                config = VisualConfig(glow=.2, animation=False)
                config.save()
                self.assertEqual(VisualConfig.load(),config)


class KittyNeonTests(unittest.TestCase):
    def test_direct_kitty_markers_and_multiplexer(self):
        env = {'TERM':'xterm-kitty','KITTY_WINDOW_ID':'1'}
        self.assertTrue(detect(Terminal(),env).kitty)
        self.assertTrue(detect(Terminal(),env).truecolor)
        for key in ('TMUX','STY'):
            self.assertFalse(detect(Terminal(),dict(env,**{key:'session'})).kitty)
        self.assertFalse(detect(io.StringIO(),env).kitty)
        self.assertEqual(VisualConfig().renderer,'kitty')

    def test_split_escape_boundaries(self):
        from src.visuals.picker import EscapeBoundary
        boundary = EscapeBoundary()
        self.assertFalse(boundary.feed(b'hello\x1b['))
        self.assertTrue(boundary.feed(b'31mhello'))
        self.assertFalse(boundary.feed(b'\x1b]title'))
        self.assertFalse(boundary.feed(b'\x1b'))
        self.assertTrue(boundary.feed(b'\\'))
        self.assertFalse(boundary.feed(b'\x1b[2'))
        self.assertTrue(boundary.feed(b'J'))
        self.assertEqual(boundary.erasures,1)
        self.assertFalse(boundary.feed(b'\x1b('))
        self.assertTrue(boundary.feed(b'B'))
        self.assertFalse(boundary.feed(b'\xe2\x95'))
        self.assertTrue(boundary.feed(b'\xad'))

    def test_animation_chunks(self):
        from src.visuals.kitty import command
        data = bytes(range(256))*32
        encoded = encode_rgba(data,32,64,17,action='f')
        chunks = encoded.split('\x1b_G')[1:]
        self.assertGreater(len(chunks),1)
        self.assertTrue(all(chunk.startswith('a=f,') for chunk in chunks))
        self.assertIn('z=83,X=1',chunks[0])

    def test_native_loop_upload_cache_and_delete(self):
        from src.visuals.kitty import KittyDecoration
        try:
            import PIL
        except ImportError:
            self.skipTest('Pillow is optional')
        config = VisualConfig()
        decoration = KittyDecoration(config)
        encoded = decoration.upload(64,48)
        self.assertIn('a=a,',encoded)
        self.assertIn('s=3,v=1',encoded)
        self.assertIn('o=z',encoded)
        self.assertEqual(decoration.upload(64,48),'')
        self.assertIn(f'd=I,i={decoration.image_id}',decoration.delete())
        self.assertIsNone(decoration.key)
        renderer = KittyRenderer(Terminal(),VisualConfig(),Capabilities(True,'kitty',True,True))
        renderer.decoration.key = (384,240)
        renderer.resize(90,28)
        self.assertIsNone(renderer.decoration.key)

    def test_static_decoration_has_no_animation(self):
        from src.visuals.kitty import KittyDecoration
        try:
            import PIL
        except ImportError:
            self.skipTest('Pillow is optional')
        encoded = KittyDecoration(VisualConfig(animation=False)).upload(64,48)
        self.assertNotIn('a=a,',encoded)
        self.assertNotIn('a=f,',encoded)

    def test_effects_are_transparent_and_flicker(self):
        from src.visuals.effects import neon_asset
        try:
            import PIL
        except ImportError:
            self.skipTest('Pillow is optional')
        first = neon_asset(160,100,.65,0)
        second = neon_asset(160,100,.65,12)
        self.assertEqual(first.mode,'RGBA')
        self.assertEqual(first.getpixel((80,50))[3],0)
        self.assertNotEqual(first.tobytes(),second.tobytes())
        self.assertGreater(first.getchannel('A').getextrema()[1],100)


class NeonGeometryTests(unittest.TestCase):
    def test_square_path_has_exact_right_angle_corners(self):
        import math
        from src.visuals.effects import square_perimeter
        points = square_perimeter(8,8,472,280)
        self.assertEqual(points[0],points[-1])
        for corner in ((8,8),(472,8),(472,280),(8,280)):
            self.assertIn(corner,points)
        for a,b in zip(points,points[1:]):
            self.assertTrue(a[0] == b[0] or a[1] == b[1])
            self.assertLessEqual(math.dist(a,b),2.01)

    def test_terminal_aspect_ratio_is_preserved(self):
        from src.visuals.kitty import raster_geometry
        for width,height,columns,rows in ((1600,600,160,30),(600,1200,60,60),(800,480,80,24)):
            rw,rh,ix,iy,radius = raster_geometry(width,height,columns,rows)
            self.assertAlmostEqual(rw/rh,width/height,delta=.02)
            self.assertAlmostEqual(ix/rw,.5/columns)
            self.assertAlmostEqual(iy/rh,.5/rows)
            self.assertEqual(radius,0)

    def test_animation_speed_does_not_depend_on_fps(self):
        from src.visuals.effects import LOOP_SECONDS
        from src.visuals.kitty import KittyDecoration
        for fps in (12,30):
            with patch('src.visuals.kitty.frame_payloads',return_value=('AA==',)) as payloads:
                KittyDecoration(VisualConfig(fps=fps)).upload(800,480,80,24)
                self.assertEqual(payloads.call_args.args[3],fps*LOOP_SECONDS)
                self.assertEqual(LOOP_SECONDS,6)


class AnimationLoadingTests(unittest.TestCase):
    def test_thirty_frames_take_one_second(self):
        from src.visuals.kitty import frame_gap
        self.assertEqual(sum(frame_gap(frame,30) for frame in range(30)),1000)
        self.assertEqual(sum(frame_gap(frame,30) for frame in range(180)),6000)

    def test_loading_can_cancel_with_full_queue(self):
        from src.visuals.kitty import AnimationLoader, KittyDecoration
        with patch('src.visuals.kitty.frame_payload',return_value='AA=='):
            loader = AnimationLoader(KittyDecoration(VisualConfig()),800,480,80,24)
            loader.close()
            self.assertFalse(loader.worker.is_alive())

    def test_worker_failure_does_not_write_to_terminal(self):
        from src.visuals.kitty import AnimationLoader, KittyDecoration
        output = io.StringIO()
        with patch('src.visuals.kitty.frame_payload',side_effect=RuntimeError('asset failure')):
            with patch('sys.stderr',output):
                loader = AnimationLoader(KittyDecoration(VisualConfig()),800,480,80,24)
                self.assertTrue(loader.done.wait(1))
                loader.close()
        self.assertIsInstance(loader.error,RuntimeError)
        self.assertEqual(output.getvalue(),'')


class TubeFlickerTests(unittest.TestCase):
    def test_events_stay_at_their_own_positions(self):
        from src.visuals.effects import flicker_events, active_flicker
        events = flicker_events(42)
        self.assertEqual(events,flicker_events(42))
        self.assertNotEqual(events,flicker_events(99))
        self.assertGreater(len({event[1] for event in events}),10)
        for center,when,duration,width,amplitude in events:
            self.assertTrue(any(position == center for position,_,_ in active_flicker(when,42)))
            self.assertTrue(any(position == center for position,_,_ in active_flicker(when+.002,42)))

    def test_flicker_is_local_and_has_no_global_wave(self):
        from src.visuals.effects import tube_level
        event = ((.25,.03,.2),)
        self.assertGreater(tube_level(.25,event),tube_level(.25,()))
        self.assertAlmostEqual(tube_level(.75,event),tube_level(.75,()))
        self.assertAlmostEqual(tube_level(.1,()),tube_level(.8,()))
        self.assertEqual(VisualConfig().glow,.45)
