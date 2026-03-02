# Quick Start Guide - Ableton to Blackbox Converter v0.3

## 🚀 5-Minute Setup

### Step 1: Check Your Python Version
```bash
python3 --version
```
✅ Need: Python 3.7 or higher  
❌ If lower or not found: See [Python Installation](#python-installation)

### Step 2: Test the Script
```bash
cd "/Users/simon/Dropbox/Blackbox Stuff/ableton_blackbox/code"
python3 xml_read.py -h
```
You should see the help message.

### Step 3: Convert Your First Project

#### For stem-based projects (audio clips):
```bash
python3 xml_read.py \
  -i "../../Ableton Files/Hack Into Your Soul/Hack into Your Soul Blackbox Export Project/Hack into Your Soul Blackbox Export.als" \
  -o "../../test_output" \
  -m
```

#### For projects with Simpler/Sampler:
```bash
python3 xml_read.py \
  -i "/path/to/your/project.als" \
  -o "/path/to/output" \
  -m
```

The `-m` flag skips sample copying (useful for first tests).

### Step 4: Check the Output
```bash
ls -la "../../test_output"
cat "../../test_output/preset.xml" | head -20
```

You should see:
- `preset.xml` file
- Sample files (if `-m` wasn't used)

### Step 5: Transfer to Blackbox
1. Copy the entire output folder to: `BLACKBOX_SD/Presets/`
2. On Blackbox: Navigate to Presets → Your folder name
3. Load and play!

## Common Workflows

### Workflow 1: Stem-Based Song Export
**Best for:** Songs arranged as audio stems in Ableton

```bash
# 1. In Ableton: Bounce each track to audio
# 2. Create a new project with just the audio clips
# 3. Convert:
python3 xml_read.py -i "MySong_Stems.als" -o "BB_MySong"

# 4. Copy BB_MySong folder to Blackbox SD card
```

### Workflow 2: Sample-Based Instruments
**Best for:** Projects using Simpler/Sampler with one-shots

```bash
# 1. Make sure your Simpler/Sampler tracks are not grouped
# 2. Ensure samples are .wav format
# 3. Convert:
python3 xml_read.py -i "MyInstrument.als" -o "BB_MyInstrument"

# 4. Check what was extracted (verbose mode):
python3 xml_read.py -i "MyInstrument.als" -o "BB_MyInstrument" -v
```

### Workflow 3: MIDI Pattern Transfer
**Best for:** MIDI clips in Session View

```bash
# 1. Create MIDI clips in Session View
# 2. Make sure they're in Simpler/Sampler tracks
# 3. Convert:
python3 xml_read.py -i "MyPatterns.als" -o "BB_MyPatterns" -v

# The -v flag shows which sequences were extracted
```

## Troubleshooting Quick Fixes

### Problem: "module 'xml.etree.ElementTree' has no attribute 'indent'"
**Solution:** You're using Python 3.7. This is already fixed in v0.3!

### Problem: Samples not copying
**Solution:** 
```bash
# Use -m flag and manually copy samples
python3 xml_read.py -i "project.als" -o "output" -m
cp /path/to/samples/*.wav output/
```

### Problem: Empty pads on Blackbox
**Cause:** Samples might not be .wav or paths are incorrect

**Solution:**
1. Use verbose mode to see what's detected:
```bash
python3 xml_read.py -i "project.als" -o "output" -m -v | grep "Sample"
```

2. Manually copy the right samples to the output folder

### Problem: Wrong tempo
**Check:** Look in the verbose output for "Found tempo"
```bash
python3 xml_read.py -i "project.als" -o "output" -m -v | grep tempo
```

## What Gets Converted?

| Ableton Element | Blackbox Element | Status |
|-----------------|------------------|--------|
| Audio clips | Loop pads | ✅ Works |
| Simpler instruments | Sample pads | ⚠️ Partial |
| Sampler instruments | Sample pads | ⚠️ Partial |
| MIDI clips | Note sequences | ⚠️ Partial |
| Project tempo | Global tempo | ✅ Works |
| Track volume | Pad volume | ❌ Not yet |
| Send effects | FX sends | ❌ Not yet |
| Arrangement | Song mode | ✅ Experimental (via `-s` flag) |

## Tips for Best Results

### ✅ Do:
- Use simple, flat track layouts
- Keep samples in .wav format
- Use Session View clips
- Test with `-m` flag first
- Check verbose output (`-v`)
- Use descriptive track names

### ❌ Avoid:
- Grouped instruments (not supported yet)
- Complex routing
- Third-party plugins (Kontakt, Serum, etc.)
- Very long file paths
- Special characters in sample names

## Python Installation

### macOS (Homebrew method):
```bash
# Install Homebrew if needed
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Install Python
brew install python3

# Verify
python3 --version  # Should be 3.11+ now
```

### macOS (Official installer):
1. Go to [python.org/downloads](https://www.python.org/downloads/)
2. Download Python 3.11 or 3.12
3. Run the installer
4. Verify: `python3 --version`

## Example Commands Reference

```bash
# Basic conversion
python3 xml_read.py -i "project.als" -o "output"

# Test mode (no sample copying)
python3 xml_read.py -i "project.als" -o "output" -m

# Debug mode (verbose)
python3 xml_read.py -i "project.als" -o "output" -v

# Full debug with manual samples
python3 xml_read.py -i "project.als" -o "output" -m -v

# Song mode (arrangement → Blackbox song sections)
python3 xml_read.py -i "project.als" -o "output" -s

# Show help
python3 xml_read.py -h
```

## Next Steps

1. ✅ Got it working? Try with your own projects!
2. 📖 Read the full [README_v2.md](README_v2.md) for details
3. 🐛 Found issues? Check [CHANGELOG.md](CHANGELOG.md) for known limitations
4. 💡 Want to improve it? The code is well-commented!

## Getting Help

If you're stuck:

1. Run with verbose mode: `-v`
2. Check what Ableton version you have
3. Look at the generated `preset.xml` structure
4. Post on the [1010music forum](https://forum.1010music.com/forum/products/blackbox/support-blackbox/43727-python-script-converting-an-ableton-live-project-to-blackbox-xml)

---

**You're ready to rock! 🎸**

