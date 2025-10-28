"""
Flask API Backend for WhatsApp Reel Video Splitter
Deploy this on PythonAnywhere, Render, or Railway
"""

from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import os
import subprocess
import json
import math
import zipfile
import shutil
from werkzeug.utils import secure_filename
import uuid
from datetime import datetime, timedelta

app = Flask(__name__)
CORS(app)  # Enable CORS for React Native

# Configuration
UPLOAD_FOLDER = 'uploads'
OUTPUT_FOLDER = 'outputs'
MAX_FILE_SIZE = 500 * 1024 * 1024  # 500MB max
ALLOWED_EXTENSIONS = {'mp4', 'mov', 'avi', 'mkv', 'webm'}

# Create folders
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def cleanup_old_files():
    """Remove files older than 1 hour"""
    now = datetime.now()
    for folder in [UPLOAD_FOLDER, OUTPUT_FOLDER]:
        for filename in os.listdir(folder):
            filepath = os.path.join(folder, filename)
            if os.path.isfile(filepath):
                file_time = datetime.fromtimestamp(os.path.getmtime(filepath))
                if now - file_time > timedelta(hours=1):
                    os.remove(filepath)
            elif os.path.isdir(filepath):
                if now - datetime.fromtimestamp(os.path.getmtime(filepath)) > timedelta(hours=1):
                    shutil.rmtree(filepath)

def get_video_duration(video_path):
    """Get video duration using ffprobe"""
    cmd = [
        'ffprobe',
        '-v', 'error',
        '-show_entries', 'format=duration',
        '-of', 'json',
        video_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    data = json.loads(result.stdout)
    return float(data['format']['duration'])

def get_video_info(video_path):
    """Get video information"""
    cmd = [
        'ffprobe',
        '-v', 'error',
        '-show_entries', 'format=size,duration:stream=width,height,codec_name',
        '-of', 'json',
        video_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    data = json.loads(result.stdout)
    
    video_stream = next((s for s in data['streams'] if s['codec_type'] == 'video'), None)
    
    return {
        'duration': float(data['format']['duration']),
        'size_mb': int(data['format']['size']) / (1024 * 1024),
        'width': video_stream.get('width') if video_stream else None,
        'height': video_stream.get('height') if video_stream else None,
        'codec': video_stream.get('codec_name') if video_stream else None
    }

def split_video(video_path, split_duration, job_id):
    """Split video using FFmpeg stream copy"""
    output_dir = os.path.join(OUTPUT_FOLDER, job_id)
    os.makedirs(output_dir, exist_ok=True)
    
    total_duration = get_video_duration(video_path)
    num_clips = math.ceil(total_duration / split_duration)
    
    clips_info = []
    
    for i in range(num_clips):
        start_time = i * split_duration
        
        if i == num_clips - 1:
            clip_duration = total_duration - start_time
        else:
            clip_duration = split_duration
        
        output_path = os.path.join(output_dir, f"reel_{i+1:02d}.mp4")
        
        cmd = [
            'ffmpeg',
            '-ss', str(start_time),
            '-i', video_path,
            '-t', str(clip_duration),
            '-c', 'copy',
            '-avoid_negative_ts', '1',
            '-y',
            output_path
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode == 0 and os.path.exists(output_path):
            file_size_mb = os.path.getsize(output_path) / (1024 * 1024)
            clips_info.append({
                'number': i + 1,
                'filename': f"reel_{i+1:02d}.mp4",
                'start': start_time,
                'end': start_time + clip_duration,
                'duration': clip_duration,
                'size_mb': round(file_size_mb, 2)
            })
    
    return clips_info, output_dir

def create_zip(output_dir, job_id):
    """Create zip file of all clips"""
    zip_path = os.path.join(OUTPUT_FOLDER, f"{job_id}.zip")
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for file in os.listdir(output_dir):
            if file.endswith('.mp4'):
                file_path = os.path.join(output_dir, file)
                zipf.write(file_path, file)
    return zip_path

@app.route('/')
def index():
    return jsonify({
        'status': 'online',
        'service': 'WhatsApp Reel Video Splitter API',
        'version': '1.0',
        'endpoints': {
            'POST /upload': 'Upload and get video info',
            'POST /split': 'Split video into reels',
            'GET /download/<job_id>': 'Download zip file'
        }
    })

@app.route('/upload', methods=['POST'])
def upload_video():
    """Upload video and return info"""
    cleanup_old_files()
    
    if 'video' not in request.files:
        return jsonify({'error': 'No video file provided'}), 400
    
    file = request.files['video']
    
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    if not allowed_file(file.filename):
        return jsonify({'error': 'Invalid file type. Allowed: mp4, mov, avi, mkv, webm'}), 400
    
    # Generate unique job ID
    job_id = str(uuid.uuid4())
    
    # Save file
    filename = secure_filename(file.filename)
    filepath = os.path.join(UPLOAD_FOLDER, f"{job_id}_{filename}")
    file.save(filepath)
    
    # Get video info
    try:
        info = get_video_info(filepath)
        info['job_id'] = job_id
        info['filename'] = filename
        return jsonify(info), 200
    except Exception as e:
        os.remove(filepath)
        return jsonify({'error': f'Failed to process video: {str(e)}'}), 500

@app.route('/split', methods=['POST'])
def split_video_endpoint():
    """Split video into clips"""
    data = request.json
    job_id = data.get('job_id')
    split_duration = data.get('split_duration', 30)
    
    if not job_id:
        return jsonify({'error': 'job_id required'}), 400
    
    # Find uploaded file
    video_files = [f for f in os.listdir(UPLOAD_FOLDER) if f.startswith(job_id)]
    if not video_files:
        return jsonify({'error': 'Video not found. Please upload again.'}), 404
    
    video_path = os.path.join(UPLOAD_FOLDER, video_files[0])
    
    try:
        # Split video
        clips_info, output_dir = split_video(video_path, split_duration, job_id)
        
        # Create zip
        zip_path = create_zip(output_dir, job_id)
        zip_size_mb = os.path.getsize(zip_path) / (1024 * 1024)
        
        return jsonify({
            'success': True,
            'job_id': job_id,
            'clips': clips_info,
            'total_clips': len(clips_info),
            'zip_size_mb': round(zip_size_mb, 2),
            'download_url': f'/download/{job_id}'
        }), 200
        
    except Exception as e:
        return jsonify({'error': f'Failed to split video: {str(e)}'}), 500

@app.route('/download/<job_id>', methods=['GET'])
def download_zip(job_id):
    """Download zip file"""
    zip_path = os.path.join(OUTPUT_FOLDER, f"{job_id}.zip")
    
    if not os.path.exists(zip_path):
        return jsonify({'error': 'File not found or expired'}), 404
    
    return send_file(
        zip_path,
        mimetype='application/zip',
        as_attachment=True,
        download_name='whatsapp_reels.zip'
    )

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({'status': 'healthy'}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)