from flask import Flask, render_template, request, jsonify, Response
import yt_dlp
import os
import tempfile
import time
import re
from urllib.parse import quote
import unicodedata

app = Flask(__name__)

# Create downloads directory
DOWNLOAD_FOLDER = tempfile.gettempdir()

def get_video_info(url):
    """Get video information and available formats"""
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'no_check_certificate': True,
        'extract_flat': False,
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            formats_dict = {}
            
            # Get audio format info
            best_audio_size = 0
            for f in info.get('formats', []):
                if f.get('acodec') != 'none' and f.get('vcodec') == 'none':
                    filesize = f.get('filesize') or f.get('filesize_approx', 0)
                    if filesize > best_audio_size:
                        best_audio_size = filesize
            
            # Process video formats
            for f in info.get('formats', []):
                height = f.get('height')
                if not height or height < 144:
                    continue
                
                vcodec = f.get('vcodec', 'none')
                acodec = f.get('acodec', 'none')
                
                # Skip audio-only formats
                if vcodec == 'none' or vcodec is None:
                    continue
                
                # Get filesize
                filesize = f.get('filesize') or f.get('filesize_approx', 0)
                
                # If video-only, add estimated audio size
                if acodec == 'none' and best_audio_size > 0:
                    filesize += best_audio_size
                
                format_note = f.get('format_note', f'{height}p')
                fps = f.get('fps', 30)
                
                # Create unique key for resolution
                key = f"{height}p"
                
                if key not in formats_dict or filesize > formats_dict[key]['filesize']:
                    formats_dict[key] = {
                        'format_id': f['format_id'],
                        'resolution': key,
                        'height': height,
                        'filesize': filesize,
                        'filesize_mb': round(filesize / (1024 * 1024), 1) if filesize else 0,
                        'ext': f.get('ext', 'mp4'),
                        'fps': fps,
                        'vcodec': vcodec,
                        'quality_label': format_note,
                    }
            
            # Convert to sorted list
            formats = list(formats_dict.values())
            formats.sort(key=lambda x: x['height'], reverse=True)
            
            # Add "Best" option at the top
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
            
            # Add audio-only option
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
        timestamp = int(time.time() * 1000)
        temp_file = os.path.join(DOWNLOAD_FOLDER, f'video_{timestamp}')
        
        # Check if audio-only
        is_audio = format_id == 'bestaudio'
        
        ydl_opts = {
            'format': format_id if not is_audio else 'bestaudio/best',
            'outtmpl': temp_file + '.%(ext)s',
            'quiet': True,
            'no_warnings': True,
            'merge_output_format': 'mp4' if not is_audio else None,
        }
        
        # Add audio extraction for MP3
        if is_audio:
            ydl_opts['postprocessors'] = [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }]
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            
            # Clean the title for filename
            clean_title = re.sub(r'[<>:"/\\|?*]', '', info['title'])
            clean_title = clean_title[:150]
            
            # Find the downloaded file
            actual_file = None
            extensions = ['mp3', 'mp4', 'webm', 'mkv']
            for ext in extensions:
                test_file = temp_file + f'.{ext}'
                if os.path.exists(test_file):
                    actual_file = test_file
                    filename = f"{clean_title}.{ext}"
                    break
            
            if not actual_file:
                return jsonify({'error': 'File not found after download'}), 500
        
        # Clean up old files
        cleanup_old_files()
        
        # Stream file with progress
        def generate():
            with open(actual_file, 'rb') as f:
                while True:
                    chunk = f.read(8192)
                    if not chunk:
                        break
                    yield chunk
            # Delete file after streaming
            try:
                os.remove(actual_file)
            except:
                pass
        
        response = Response(generate(), mimetype='application/octet-stream')
        # Create ASCII-safe fallback filename to avoid Latin-1 encoding errors
        try:
            safe_name = unicodedata.normalize('NFKD', filename).encode('ascii', 'ignore').decode('ascii')
        except Exception:
            safe_name = filename
        if not safe_name:
            safe_name = 'video'

        # RFC5987 encoded filename* parameter for UTF-8 filenames
        try:
            encoded_name = quote(filename)
            response.headers['Content-Disposition'] = f"attachment; filename=\"{safe_name}\"; filename*=UTF-8''{encoded_name}"
        except Exception:
            # Fallback to basic header
            response.headers['Content-Disposition'] = f'attachment; filename="{safe_name}"'

        response.headers['Content-Length'] = str(os.path.getsize(actual_file))
        
        return response
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def cleanup_old_files():
    """Clean up temporary video files older than 1 hour"""
    current_time = time.time()
    
    for filename in os.listdir(DOWNLOAD_FOLDER):
        if filename.startswith('video_'):
            filepath = os.path.join(DOWNLOAD_FOLDER, filename)
            try:
                file_age = current_time - os.path.getmtime(filepath)
                if file_age > 3600:  # 1 hour
                    os.remove(filepath)
            except:
                pass

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))  # Render default port is 10000
    app.run(host="0.0.0.0", port=port)