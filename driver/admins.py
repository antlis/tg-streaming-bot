from typing import List
from pyrogram.types import Chat
from pyrogram.enums import ChatMembersFilter, ChatMemberStatus
from cache.admins import get as gett, set


async def get_administrators(chat: Chat) -> List[int]:
    get = gett(chat.id)

    if get:
        return get
    else:
        to_set = []
        async for administrator in chat.get_members(
            filter=ChatMembersFilter.ADMINISTRATORS
        ):
            priv = administrator.privileges
            if administrator.status == ChatMemberStatus.OWNER or (
                priv and priv.can_manage_video_chats
            ):
                to_set.append(administrator.user.id)

        set(chat.id, to_set)
        return await get_administrators(chat)
