# Databases

# Project: Redis URL Shortener
This project is a high-performance link shortening service built around the capabilities of **Redis**. The main goal of the project is to demonstrate effective work with various types of NoSQL data and the automation of the data lifecycle in RAM.

## The role of Redis in architecture

Unlike traditional SQL databases, Redis is used here not just as a cache, but as the main storage using the following structures:

* **Strings**: Storing pairs of `short_code: long_url` to ensure a redirect with minimal delay.
* **Hashes**: Storing the structured metadata of each link (creation time, click-through limit, `expire_ts`).
* **Sorted Sets (ZSET)**: Global index of visits. Allows you to instantly get sorted lists of popular links and manage the cleaning queue.
* **Sets**: Organization of tags for grouping links.

## Main features

* **Rate Limiting**: Overload protection (no more than 5 requests per minute from one IP) via the `INCR` and `EXPIRE` mechanisms.
* **Automatic TTL**: Each link has its own lifetime, controlled directly by the Redis core.
* **Limited clicks**: The link can be deleted automatically after reaching the set number of clicks.
* **Atomic operations**: Using the 'Redis Pipeline' to ensure data integrity when writing multiple keys simultaneously.

## Installation and launch

### 1. Cloning a repository
```bash
git clone https://github.com/Kliooo/Databases/
cd Databases
```

### 2. Environment preparation
Python 3.x must be installed and the Redis server is running (port 6379 by default).
The best option is to use **WSL** for this:

Install the necessary dependencies (in Windows):
```bash
pip install flask redis
```
Install the necessary dependencies (in WSL):
```bash
sudo apt install redis-server
```

1. Starting the server (in WSL):
   ```bash
   sudo service redis-server start
   ```
   
2. Checking the server operation (in WSL):
   ```bash
   ss -tulpn | grep 6379
   ```

3. Stopping the server (in WSL):
   ```bash
   sudo service redis-server stop
   ```
4. I recommend disabling server autorun when opening WSL:
   ```bash
   sudo systemctl disable redis-server
   ```

### 3. Configuring External Access (ngrok)
To make your links accessible from the internet, you need to use **ngrok** to create a public tunnel.

1. Download ngrok for your OS:
   * [Official ngrok Download](https://ngrok.com/download)
   * Unpack the archive and place `ngrok.exe` in the project directory.
  
2. Get and activate your Authtoken:
   * [Authtoken](https://dashboard.ngrok.com/get-started/your-authtoken)
  
   Activate autotoken in project terminal (in Windows):

   ```bash
   ./ngrok.exe authtoken YOUR_AUTHTOKEN_HERE
   ```

2. Launch ngrok:
   ```bash
   ./ngrok.exe http 5000
   ```
3. Copy the received Forwarding address.
4. In the file `redis_link_shortening.py` replace the value of the `NGROK_DOMAIN` variable with your current domain.

### 4. Launching the app
```bash
python redis_link_shortening.py
```
