import asyncio
from xvideos_api import Client
# Initialize a Client object

async def do_something():    
    client = Client()
    
    # Fetch a video
    video_object = await client.get_video("https://www.xvideos.com/video.hkpbphd0089/casey_calvert_and_remy_lacroix_lesbo_fun")
    
    # Information from Video objects
    print(video_object.title)
    print(video_object.likes)
    # Download the video
    
    await video_object.download(downloader="threaded", quality="best")

asyncio.run(do_something())    
# SEE DOCUMENTATION FOR MOREv


OBS: o arquivo file.zip eo que vai para a producao quando for
configurado um webholk