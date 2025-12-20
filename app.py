import os
import hashlib
import random
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, FileResponse
import time
import json

app = FastAPI()

# Vercel ephemeral storage path
STORAGE_PATH = "/tmp"

files = {}  # 存储文件的分块信息
history = {}  # 存储上传历史，主要是永远保留的文件数据
bucket = {} # 文件id、上传id、下载次数、过期时间，主要是业务数据，储存有效的下载信息
userData = {} # 用户数据

# Helper to get full path
def get_path(filename):
    return os.path.join(STORAGE_PATH, filename)

# 读取history和bucket
if os.path.exists(get_path('history.json')):
    with open(get_path('history.json'), 'r') as f:
        history = json.loads(f.read())
if os.path.exists(get_path('bucket.json')):
    with open(get_path('bucket.json'), 'r') as f:
        bucket = json.loads(f.read())
if os.path.exists(get_path('files.json')):
    with open(get_path('files.json'), 'r') as f:
        files = json.loads(f.read())


# 创建文件夹
os.makedirs(get_path('uploads/parts'), exist_ok=True)
os.makedirs(get_path('uploads/results'), exist_ok=True)

CHUNK_SIZE = 4 * 1024 * 1024  # 4MB to fit Vercel's 4.5MB payload limit
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2GB 最大文件大小

def load_user_data():
    global userData
    if os.path.exists(get_path('userData.json')):
        with open(get_path('userData.json'), 'r') as f:
            userData = json.loads(f.read())
    else:
        userData = {}

def get_code():
    # 获取一个6位数取件码
    return ''.join([str(random.randint(0, 9)) for _ in range(6)])

def deduct_credit(token):
    # Always return success, effectively infinite upload chance
    return {"upload_chance": 999999}
        
def get_userid(token):
    # Return a default user ID
    return "default_user"

def save_user_data():
    with open(get_path('userData.json'), 'w') as f:
        f.write(json.dumps(userData))

def get_remain_credit(token):
    return 999999

def check_upload_permission(token, chunk_count):
    """检查用户是否有足够的积分上传文件"""
    return True, "积分充足"

# 抽取业务逻辑：初始化上传
def init_upload(filename: str, file_size: int, max_downloads: int = 2, max_retention: int = 2):
    file_id = hashlib.md5((filename + str(random.random())).encode()).hexdigest()
    token = hashlib.md5((file_id + str(random.random())).encode()).hexdigest()[:16]
    os.makedirs(get_path(f'uploads/parts/{file_id}'), exist_ok=True)
    # 整除得到分块数量，因为舍弃余数，所以需要加 1
    chunk_count = file_size // CHUNK_SIZE + 1
    chunks = []
    for i in range(chunk_count):
        chunks.append({
            'chunk_id': i,
            'status': False,
            'path': ''
        })
    files[file_id] = {
        "filename": filename,
        "chunk_count": chunk_count,
        "chunks": chunks,
        "token": token,
        "max_downloads": max_downloads,
        "max_retention": max_retention
    }
    return file_id, token

# 抽取业务逻辑：保存上传的分块
def save_chunk(file_id: str, chunk_id: int, data: bytes, token: str):
    if file_id not in files:
        raise HTTPException(status_code=404, detail="file_id not found")
    if files[file_id]["token"] != token:
        raise HTTPException(status_code=403, detail="token error")
    total_chunks = files[file_id]["chunk_count"]
    if not (0 <= chunk_id < total_chunks):
        raise HTTPException(status_code=404, detail="chunk_id not found")
    if files[file_id]["chunks"][chunk_id]['status']:
        return "chunk already uploaded"
    files[file_id]["chunks"][chunk_id]['status'] = True
    filepath = get_path(f'uploads/parts/{file_id}/{chunk_id}.chk')
    with open(filepath, 'wb') as f:
        f.write(data)
    files[file_id]["chunks"][chunk_id]['path'] = f"{chunk_id}.chk"
    return "chunk uploaded"

# 抽取业务逻辑：上传完成后合并分块
def merge_chunks(file_id: str, userid: str):
    if file_id not in files:
        raise HTTPException(status_code=404, detail="file_id not found")
    filename = files[file_id]["filename"]
    chunk_count = files[file_id]["chunk_count"]
    chunk_paths = [get_path(f'uploads/parts/{file_id}/{i}.chk') for i in range(chunk_count)]
    with open(get_path(f'uploads/results/{file_id}.rst'), 'wb') as f:
        for chunk_path in chunk_paths:
            with open(chunk_path, 'rb') as chunk_file:
                f.write(chunk_file.read())
    # 加入到上传历史
    history[file_id] = {
        "filename": filename,
        "path": get_path(f"uploads/results/{file_id}.rst"),
        "size": os.path.getsize(get_path(f"uploads/results/{file_id}.rst")),
        "time": time.time(),
    }

    code = get_code()
    
    max_downloads = files[file_id].get("max_downloads", 2)
    max_retention = files[file_id].get("max_retention", 2)
    
    if bucket.get(code, None) is None:
        bucket[code] = {
            "file_id": file_id,
            "user_id": userid,
            "upload_id": code,
            "download_count": 0,
            "avaliable_download_count": max_downloads,
            "upload_time": time.time(),
            "expired_time": time.time() + (max_retention * 3600)
        }

    save_history()
    delete_tmp_files(file_id)
    
    return "merge success", code

def delete_expire_files():
    try:
        # 删除bucket中所有的：1. 过期文件 2. 下载次数大于等于可下载次数的文件
        expired_keys = []
        for k, v in bucket.items():
            if v["expired_time"] < time.time() or v["download_count"] >= v["avaliable_download_count"]:
                # 删除result文件
                try:
                    file_id = v["file_id"]
                    if file_id in history:
                        file_path = history[file_id]["path"]
                        if os.path.exists(file_path):
                            os.remove(file_path)
                        
                        # 检查是否还有其他bucket项引用相同file_id
                        referenced = False
                        for bk, bv in bucket.items():
                            if bk != k and bv["file_id"] == file_id:
                                referenced = True
                                break
                        
                        # 如果没有其他引用，从history中删除
                        if not referenced:
                            del history[file_id]
                    
                    expired_keys.append(k)
                except Exception as e:
                    print(f"清理文件时出错: {e}")
                    
        # 从bucket中删除过期项
        for k in expired_keys:
            del bucket[k]
            
        # 清理孤立的分块文件夹
        active_file_ids = set(info["file_id"] for info in bucket.values())
        if os.path.exists(get_path('uploads/parts')):
            for folder in os.listdir(get_path('uploads/parts')):
                if folder not in active_file_ids:
                    folder_path = os.path.join(get_path('uploads/parts'), folder)
                    if os.path.isdir(folder_path):
                        try:
                            # 删除文件夹中的所有文件
                            for file in os.listdir(folder_path):
                                os.remove(os.path.join(folder_path, file))
                            # 删除空文件夹
                            os.rmdir(folder_path)
                        except Exception as e:
                            print(f"删除文件夹 {folder_path} 时出错: {e}")
                            
        save_history()
    except Exception as e:
        print(f"清理过期文件时出错: {e}")

def delete_tmp_files(file_id):
    # 删除分块文件
    for i in range(files[file_id]["chunk_count"]):
        try:
            os.remove(get_path(f'uploads/parts/{file_id}/{i}.chk'))
        except FileNotFoundError:
            pass  # 忽略已不存在的文件
            
    # 删除空文件夹
    try:
        os.rmdir(get_path(f'uploads/parts/{file_id}'))
    except OSError:
        pass  # 忽略无法删除的文件夹（可能非空）

def get_file_info(code):
    if bucket.get(code, None) is None:
        return None
    file_id = bucket[code]["file_id"]
    if history.get(file_id, None) is None:
        return None
    
    # 如果没有下载次数了，返回未找到文件
    if bucket[code]["download_count"] >= bucket[code]["avaliable_download_count"]:
        # 清除过期文件
        delete_expire_files()
        return {"error": "未找到文件"}
    
    ret = {
        "filename": history[file_id]["filename"],
        "size": history[file_id]["size"],
        "time": history[file_id]["time"],
        "code": code,
        "remain_download": bucket[code]["avaliable_download_count"] - bucket[code]["download_count"],
        "expired_time": bucket[code]["expired_time"]
    }
    return ret
    

def save_history():
    with open(get_path('history.json'), 'w') as f:
        f.write(json.dumps(history))
    with open(get_path('bucket.json'), 'w') as f:
        f.write(json.dumps(bucket))
    with open(get_path('files.json'), 'w') as f:
        f.write(json.dumps(files))

# Static file routes removed for Vercel deployment

@app.post("/upload/start")
async def start_upload(request: Request):
    # 从查询参数中获取文件名和文件大小
    params = request.query_params
    filename = params.get("filename")
    file_size = params.get("file_size")
    # user_token = params.get("utoken") # Removed token check
    
    if not filename or not file_size:
        return JSONResponse({"code": "400", "message": "filename or file_size is required"}, status_code=400)
    try:
        file_size = int(file_size)
    except ValueError:
        return JSONResponse({"code": "400", "message": "file_size is invalid"}, status_code=400)
    
    # 计算分块数量
    chunk_count = file_size // CHUNK_SIZE + 1
    
    # 检查文件大小是否超过限制
    if file_size > MAX_FILE_SIZE:
        return JSONResponse({"code": "413", "message": f"文件大小超过限制，最大允许{MAX_FILE_SIZE}字节"}, status_code=413)
    
    max_downloads = int(params.get("max_downloads", 2))
    max_retention = int(params.get("max_retention", 2))
    
    file_id, token = init_upload(filename, file_size, max_downloads, max_retention)
    
    # 返回剩余积分信息 (Always return infinite/dummy)
    credit_info = {
        "remain_credit": 999999,
        "required_credit": chunk_count
    }
    
    return JSONResponse({
        "file_id": file_id, 
        "token": token, 
        **credit_info
    })

@app.post("/upload/chunk")
async def upload_chunk(request: Request):
    params = request.query_params
    file_id = params.get("file_id")
    chunk_id_str = params.get("chunk_id")
    token = params.get("token")
    
    if not file_id or chunk_id_str is None:
        raise HTTPException(status_code=410, detail="缺少file_id或chunk_id参数")
    try:
        chunk_id = int(chunk_id_str)
    except ValueError:
        raise HTTPException(status_code=411, detail="invalid chunk_id")
    
    # Removed user token and credit checks
    
    data = await request.body()
    msg = save_chunk(file_id, chunk_id, data, token)
    
    # Removed credit deduction
    
    # 返回剩余积分
    remain_credit = 999999
        
    response = {"message": msg}
    if remain_credit is not None:
        response["remain_credit"] = remain_credit
        
    return JSONResponse(response)

@app.post("/upload/finish")
async def finish_upload(request: Request):
    params = request.query_params
    file_id = params.get("file_id")
    token = params.get("token")
    if not file_id or not token:
        raise HTTPException(status_code=400, detail="缺少file_id或token参数")
    if files.get(file_id, {}).get("token") != token:
        raise HTTPException(status_code=403, detail="token错误")
    userdata = get_userid(token)
    msg, code = merge_chunks(file_id, userdata)
    return JSONResponse({"message": msg, "code": code})

@app.get("/info/{code}")
async def get_info(code: str):
    ret = get_file_info(code)
    if ret is None:
        raise HTTPException(status_code=404, detail="code not found")
    return JSONResponse(ret)

@app.get("/download/{code}")
async def download_file(code: str):
    info = get_file_info(code)
    if info is None:
        raise HTTPException(status_code=404, detail="Invalid code")
    file_id = bucket[code]["file_id"]
    file_path = history[file_id]["path"]
    if not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="File not found")
    # Increment the download count and save updated history
    if bucket[code]["download_count"] >= bucket[code]["avaliable_download_count"]:
        raise HTTPException(status_code=403, detail="Download limit exceeded")
    bucket[code]["download_count"] += 1
    save_history()
    # 强制下载：设置 media_type 为 application/octet-stream，并指定上传时的文件名
    return FileResponse(
        file_path,
        media_type="application/octet-stream",
        filename=history[file_id]["filename"]
    )

@app.get("/api/py/hello")
def hello():
    return {"message": "Hello from FastAPI on Vercel!"}

if __name__ == '__main__':
    import uvicorn
    import threading
    import time
    
    # 定期清理过期文件的线程
    def cleanup_task():
        while True:
            try:
                delete_expire_files()
                time.sleep(3600)  # 每小时清理一次
            except Exception as e:
                print(f"清理任务出错: {e}")
                time.sleep(60)  # 出错后等待一分钟再次尝试
    
    # 启动清理线程
    cleanup_thread = threading.Thread(target=cleanup_task, daemon=True)
    cleanup_thread.start()
    
    # 启动服务器
    uvicorn.run("index:app", host="0.0.0.0", port=8000, reload=True)