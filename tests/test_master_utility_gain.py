"""
Unit smoke tests for master Utility StereoGain parsing (Ableton ALS-style XML).

Run from repo root: ``python3 -m unittest tests/test_master_utility_gain``.
"""
import math
import os
import shutil
import sys
import tempfile
import unittest
import array
import wave
import xml.etree.ElementTree as ET

_REPO = os.path.dirname(os.path.abspath(__file__))
_CODE = os.path.join(os.path.dirname(_REPO), 'code')
sys.path.insert(0, os.path.abspath(_CODE))

import xml_read  # noqa: E402


def _minimal_ls_with_stereo_gain(gain_manual='1'):
    xml = r"""<?xml version="1.0" encoding="UTF-8"?>
<Ableton MajorVersion="5">
  <LiveSet>
    <MainTrack>
      <DeviceChain>
        <Mixer />
        <DeviceChain>
          <Devices>
            <StereoGain>
              <On>
                <Manual Value="true" />
              </On>
              <Gain>
                <Manual Value="__GAIN__" />
              </Gain>
            </StereoGain>
          </Devices>
          <SignalModulations />
        </DeviceChain>
      </DeviceChain>
    </MainTrack>
  </LiveSet>
</Ableton>
""".replace('__GAIN__', str(gain_manual))
    return ET.fromstring(xml)


class TestUtilityGainParse(unittest.TestCase):
    def test_linear_to_db_unity(self):
        root = _minimal_ls_with_stereo_gain('1')
        db = xml_read.extract_master_utility_gain_db(root)
        self.assertIsNotNone(db)
        self.assertAlmostEqual(db, 0.0)

    def test_plus_six_db_linear(self):
        lin = float(10.0 ** (6.0 / 20.0))
        root = _minimal_ls_with_stereo_gain(str(lin))
        db = xml_read.extract_master_utility_gain_db(root)
        self.assertIsNotNone(db)
        self.assertAlmostEqual(db, 6.0)

    def test_bypass_skips_first_utility_reads_second(self):
        lin = float(10.0 ** (6.0 / 20.0))
        xml_text = rf"""<?xml version="1.0" encoding="UTF-8"?>
<Ableton MajorVersion="5">
  <LiveSet>
    <MainTrack>
      <DeviceChain>
        <DeviceChain>
          <Devices>
            <StereoGain>
              <On><Manual Value="false" /></On>
              <Gain><Manual Value="999" /></Gain>
            </StereoGain>
            <StereoGain>
              <Gain><Manual Value="{lin}" /></Gain>
            </StereoGain>
          </Devices>
        </DeviceChain>
      </DeviceChain>
    </MainTrack>
  </LiveSet>
</Ableton>
"""
        root = ET.fromstring(xml_text)
        db = xml_read.extract_master_utility_gain_db(root)
        self.assertAlmostEqual(db, 6.0)


class TestPcmGainBake(unittest.TestCase):
    """RMS scales ~linearly after PCM16 multiply."""

    def test_pcm16_rms_follows_multiplier(self):
        rng = tempfile.mkdtemp()
        try:
            sr = 48000
            n = int(0.08 * sr)

            pcm = []
            for i in range(n):
                t = float(i) / float(sr)
                samp = math.sin(2 * math.pi * 440 * t)
                pcm.append(max(-32768, min(32767, int(round(samp * (32767 * 0.35))))))

            src = os.path.join(rng, 'in.wav')
            dst = os.path.join(rng, 'out.wav')

            with wave.open(src, 'wb') as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sr)
                wf.writeframes(array.array('h', pcm).tobytes())

            lin = float(10.0 ** (6.0 / 20.0))
            xml_read.copy_wav_with_master_gain(src, dst, lin)

            def _rms(path):
                with wave.open(path, 'rb') as wf:
                    raw_b = wf.readframes(wf.getnframes())
                a = array.array('h')
                a.frombytes(raw_b)
                if not len(a):
                    return 0.0
                return math.sqrt(sum(float(x) * float(x) for x in a) / float(len(a)))

            rms_i = _rms(src)
            rms_o = _rms(dst)
            self.assertGreater(rms_i, 0)
            ratio = rms_o / rms_i
            self.assertAlmostEqual(ratio, lin, delta=0.03)
        finally:
            shutil.rmtree(rng, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
