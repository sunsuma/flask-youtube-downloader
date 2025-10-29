from flask import Flask, render_template, request, jsonify, Response
import yt_dlp
import os
import tempfile
import time
import re
from urllib.parse import quote
import unicodedata
import shutil

app = Flask(__name__)

# Use system temp folder for downloads
DOWNLOAD_FOLDER = tempfile.gettempdir()


def get_video_info(url):
    """Fetch video information and available formats safely."""
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'no_check_certificate': True,
        'extract_flat': False,
    }

    try:
        # Prevent yt_dlp from raising unexpected keyword argument errors when generating bug reports
        yt_dlp.utils.bug_reports_message = lambda *args, **kwargs: ''

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

            if not info:
                return {'error': 'Could not extract video information'}

            formats_dict = {}
            best_audio_size = 0

            for f in info.get('formats', []):
                if f.get('acodec') != 'none' and f.get('vcodec') == 'none':
                    filesize = f.get('filesize') or f.get('filesize_approx', 0)
                    if filesize > best_audio_size:
                        best_audio_size = filesize

            for f in info.get('formats', []):
                height = f.get('height')
                if not height or height < 144:
                    continue

                vcodec = f.get('vcodec', 'none')
                acodec = f.get('acodec', 'none')
                if vcodec == 'none':
                    continue

                filesize = f.get('filesize') or f.get('filesize_approx', 0)
                if acodec == 'none' and best_audio_size > 0:
                    filesize += best_audio_size

                key = f"{height}p"
                if key not in formats_dict or filesize > formats_dict[key]['filesize']:
                    formats_dict[key] = {
                        'format_id': f['format_id'],
                        'resolution': key,
                        'height': height,
                        'filesize': filesize,
                        'filesize_mb': round(filesize / (1024 * 1024), 1) if filesize else 0,
                        'ext': f.get('ext', 'mp4'),
                        'fps': f.get('fps', 30),
                        'vcodec': vcodec,
                        'quality_label': f.get('format_note', f'{height}p'),
                    }

            formats = list(formats_dict.values())
            formats.sort(key=lambda x: x['height'], reverse=True)

            if formats:
                formats.insert(0, {
                    'format_id': 'bestvideo+bestaudio/best',
                    'resolution': 'Best Available',
                    'height': 9999,
                    'filesize': formats[0]['filesize'],
                    'filesize_mb': formats[0]['filesize_mb'],
                    'ext': 'mp4',
                    'fps': 60,
                    'quality_label': 'Highest Quality',
                })

            formats.append({
                'format_id': 'bestaudio',
                'resolution': 'Audio Only',
                'height': 0,
                'filesize': best_audio_size,
                'filesize_mb': round(best_audio_size / (1024 * 1024), 1) if best_audio_size else 0,
                'ext': 'mp3',
                'fps': 0,
                'quality_label': 'MP3 Audio',
            })

            return {
                'title': info.get('title', 'Unknown Title'),
                'thumbnail': info.get('thumbnail', ''),
                'duration': info.get('duration', 0),
                'uploader': info.get('uploader', 'Unknown'),
                'view_count': info.get('view_count', 0),
                'formats': formats,
            }

    except Exception as e:
        return {'error': f'Failed to fetch video info: {str(e)}'}


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/get_info', methods=['POST'])
def get_info():
    """Return available formats for a given video URL."""
    url = request.json.get('url')
    if not url:
        return jsonify({'error': 'No URL provided'}), 400

    info = get_video_info(url)
    return jsonify(info)


@app.route('/download', methods=['POST'])
def download():
    """Download a video or audio file."""
    url = request.json.get('url')
    format_id = request.json.get('format_id')

    if not url or not format_id:
        return jsonify({'error': 'Missing parameters'}), 400

    try:
        timestamp = int(time.time() * 1000)
        temp_file = os.path.join(DOWNLOAD_FOLDER, f'video_{timestamp}')
        is_audio = format_id == 'bestaudio'
        ffmpeg_available = shutil.which('ffmpeg') is not None

        ydl_format = 'best' if ('+' in format_id and not ffmpeg_available) else format_id
        ydl_opts = {
            'format': ydl_format,
            'outtmpl': temp_file + '.%(ext)s',
            'quiet': True,
            'no_warnings': True,
        }

        if not is_audio and ffmpeg_available:
            ydl_opts['merge_output_format'] = 'mp4'

        if is_audio and ffmpeg_available:
            ydl_opts['postprocessors'] = [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }]

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)

            title = info.get('title', 'video')
            clean_title = re.sub(r'[<>:"/\\|?*]', '', title)[:150]

            actual_file = None
            for ext in ['mp3', 'mp4', 'webm', 'mkv']:
                test_file = temp_file + f'.{ext}'
                if os.path.exists(test_file):
                    actual_file = test_file
                    filename = f"{clean_title}.{ext}"
                    break

            if not actual_file:
                return jsonify({'error': 'Download failed or file not found'}), 500

        cleanup_old_files()

        def generate():
            with open(actual_file, 'rb') as f:
                while chunk := f.read(8192):
                    yield chunk
            try:
                os.remove(actual_file)
            except:
                pass

        response = Response(generate(), mimetype='application/octet-stream')

        safe_name = unicodedata.normalize('NFKD', filename).encode('ascii', 'ignore').decode('ascii') or 'video'
        encoded_name = quote(filename)
        response.headers['Content-Disposition'] = f"attachment; filename=\"{safe_name}\"; filename*=UTF-8''{encoded_name}"
        response.headers['Content-Length'] = str(os.path.getsize(actual_file))

        return response

    except Exception as e:
        return jsonify({'error': str(e)}), 500


def cleanup_old_files():
    """Clean up temporary files older than 1 hour."""
    now = time.time()
    for fname in os.listdir(DOWNLOAD_FOLDER):
        if fname.startswith('video_'):
            fpath = os.path.join(DOWNLOAD_FOLDER, fname)
            try:
                if now - os.path.getmtime(fpath) > 3600:
                    os.remove(fpath)
            except:
                pass


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
