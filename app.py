from flask import Flask, render_template, request, jsonify, send_file
import yt_dlp
import os
import tempfile
from pathlib import Path

app = Flask(__name__)

# Create downloads directory
DOWNLOAD_FOLDER = tempfile.gettempdir()


# Temporary cookie file path
cookiefile_path = os.path.join(tempfile.gettempdir(), "cookies.txt")

# If Render environment variable YT_COOKIES exists, write it to a temp file
if os.environ.get("YT_COOKIES"):
    with open(cookiefile_path, "w", encoding="utf-8") as f:
        f.write(os.environ["YT_COOKIES"])
# Else if running locally and cookies.txt exists, use it
elif os.path.exists("cookies.txt"):
    cookiefile_path = os.path.abspath("cookies.txt")
# Else, no cookies available
else:
    cookiefile_path = None

print(f"✅ Using cookie file: {cookiefile_path if cookiefile_path else 'No cookies loaded'}")


def get_video_info(url):
    """Get video information and available formats - OPTIMIZED"""
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'no_check_certificate': True,
        'extract_flat': False,
        'youtube_include_dash_manifest': True,
        'cookiefile': cookiefile_path,
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            formats_dict = {}
            
            # Process all formats in single pass
            for f in info.get('formats', []):
                height = f.get('height')
                if not height or height < 144:  # Skip very low quality
                    continue
                
                vcodec = f.get('vcodec', 'none')
                acodec = f.get('acodec', 'none')
                
                # Skip audio-only or formats without proper codecs
                if vcodec == 'none' or vcodec is None:
                    continue
                
                # Get filesize - prioritize filesize over filesize_approx
                filesize = f.get('filesize')
                if not filesize:
                    filesize = f.get('filesize_approx', 0)
                
                # For formats with video but no audio, estimate audio size
                format_note = f.get('format_note', '').lower()
                has_audio = acodec != 'none' and acodec is not None
                
                # If no audio, add estimated audio size (typically 128kbps * duration)
                if not has_audio and filesize and info.get('duration'):
                    # Estimate audio: ~128kbps = 16KB/s
                    estimated_audio = int(info['duration'] * 16 * 1024)
                    filesize += estimated_audio
                
                # Create format entry
                if height not in formats_dict:
                    formats_dict[height] = {
                        'format_id': f['format_id'],
                        'resolution': f'{height}p',
                        'height': height,
                        'filesize': filesize,
                        'filesize_mb': round(filesize / (1024 * 1024), 2) if filesize else 0,
                        'ext': f.get('ext', 'mp4'),
                        'quality_label': format_note or f'{height}p',
                    }
                else:
                    # Keep the one with larger filesize (better quality)
                    if filesize > formats_dict[height]['filesize']:
                        formats_dict[height].update({
                            'format_id': f['format_id'],
                            'filesize': filesize,
                            'filesize_mb': round(filesize / (1024 * 1024), 2) if filesize else 0,
                            'ext': f.get('ext', 'mp4'),
                        })
            
            # Convert to sorted list
            formats = list(formats_dict.values())
            formats.sort(key=lambda x: x['height'], reverse=True)
            
            # If no formats found, try simpler approach
            if not formats:
                formats = [{
                    'format_id': 'best',
                    'resolution': 'Best',
                    'height': 9999,
                    'filesize': 0,
                    'filesize_mb': 0,
                    'ext': 'mp4',
                    'quality_label': 'Best Available',
                }]
            
            return {
                'title': info.get('title', 'Unknown Title'),
                'thumbnail': info.get('thumbnail', ''),
                'duration': info.get('duration', 0),
                'formats': formats
            }
    except Exception as e:
        return {'error': str(e)}

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/get_info', methods=['POST'])
def get_info():
    """Get video information"""
    url = request.json.get('url')
    if not url:
        return jsonify({'error': 'No URL provided'}), 400
    
    info = get_video_info(url)
    return jsonify(info)

@app.route('/download', methods=['POST'])
def download():
    """Download video in specified format"""
    url = request.json.get('url')
    format_id = request.json.get('format_id')
    
    if not url or not format_id:
        return jsonify({'error': 'Missing parameters'}), 400
    
    try:
        # Create unique temporary file based on timestamp
        import time
        import re
        timestamp = int(time.time() * 1000)
        temp_file = os.path.join(DOWNLOAD_FOLDER, f'video_{timestamp}_{format_id}')
        
        ydl_opts = {
            'format': format_id,
            'outtmpl': temp_file + '.%(ext)s',
            'quiet': True,
            # Preserve original audio and subtitles
            'writesubtitles': True,
            'writeautomaticsub': False,
            'subtitleslangs': ['all'],
            'embedsubtitles': True,
            # Keep all audio tracks and metadata
            'keepvideo': False,
            'postprocessors': [{
                'key': 'FFmpegEmbedSubtitle',
            }],
            # Preserve original language
            'prefer_ffmpeg': True,
            'merge_output_format': 'mp4',
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            
            # Clean the title for filename (remove invalid characters)
            clean_title = re.sub(r'[<>:"/\\|?*]', '', info['title'])
            clean_title = clean_title[:200]  # Limit length
            filename = f"{clean_title}.mp4"
            
            # Find the actual downloaded file
            actual_file = temp_file + '.mp4'
            if not os.path.exists(actual_file):
                # Check for other extensions
                for ext in ['webm', 'mkv', 'mp4']:
                    test_file = temp_file + f'.{ext}'
                    if os.path.exists(test_file):
                        actual_file = test_file
                        break
        
        # Clean up old temp files
        cleanup_old_files()
            
        return send_file(
            actual_file,
            as_attachment=True,
            download_name=filename,
            mimetype='video/mp4'
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def cleanup_old_files():
    """Clean up temporary video files older than 1 hour"""
    import time
    current_time = time.time()
    
    for filename in os.listdir(DOWNLOAD_FOLDER):
        if filename.startswith('video_') and filename.endswith('.mp4'):
            filepath = os.path.join(DOWNLOAD_FOLDER, filename)
            try:
                file_age = current_time - os.path.getmtime(filepath)
                if file_age > 3600:  # 1 hour
                    os.remove(filepath)
            except:
                pass

# if __name__ == '__main__':
#     app.run(debug=True, port=5000)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))  # Render default port is 10000
    app.run(host="0.0.0.0", port=port)