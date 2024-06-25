# bilibili-to-podcast
将哔哩哔哩的视频合集转化为播客链接在podcast应用中播放。

## 使用方式
参照`config.yaml`配置合集的uid和sid。 

例如：`https://space.bilibili.com/4698846/channel/collectiondetail?sid=705718&ctype=0` :
```yaml
channel:
  - uid: 391930545
    sid: 598034
```

## TODO
- [ ] docker build
- [ ] github action
- [ ] support OSS upload
- [ ] ai视频总结
- [ ] 分P视频转换为podcast

## bilibili-api
https://nemo2011.github.io/bilibili-api/#/